# Halide scheduling and the loop nest

This document explains how Halide turns a *scheduled pipeline* into a *loop
nest*, at the level of detail needed to predict the output of
`Func::print_loop_nest()` by hand. It is built up holistically: each section
adds to a single mental model rather than describing an isolated feature.

> Scope of this revision: the programming model, pure (non-update) Funcs, the
> default (inline) schedule, `compute_root`, `compute_at`, `store_at` /
> `store_root`, `hoist_storage` / `hoist_storage_root`, and the
> `print_loop_nest()` output format. Splitting, fusing, reordering, update
> definitions, wrappers (`in`/`clone_in`), and GPU scheduling are deferred to
> later revisions. Where one of those interacts with the model below in a way
> you can already observe, it is flagged explicitly.

---

## 1. The programming model

A Halide program is built in two separable parts:

1. **The algorithm** — *what* each pixel's value is. You declare `Func`s and
   define them as pure mathematical functions of their argument `Var`s.
2. **The schedule** — *when and where* each value is computed and stored. This
   is expressed by scheduling directives (`compute_root`, `compute_at`, …)
   attached to each `Func`.

Crucially, the schedule never changes the *result*; it only changes the order
of computation, the amount of redundant recomputation, and the temporary
storage used. This document is only about how the schedule maps to a loop nest.

### Objects and their conceptual state

* **`Var`** — a name for a dimension / loop variable. It carries no state
  beyond its identity (its name). Vars are the formal parameters of a pure
  definition and the handles you later name in scheduling calls.

* **`Func`** — a *handle* to a shared, mutable function definition. Copying a
  `Func` produces another handle to the *same* underlying function; scheduling
  through either handle affects the one function. The conceptual state of a
  Func is:
    * its **name** (used for printing; see §6,
      and computation order tie-breaking, see §7),
    * its ordered list of **pure dimensions** (the `Var`s on the left-hand
      side of its definition; the first listed is the *innermost* loop — see
      §5),
    * the set of **other Funcs it reads from** (its *producers*), derived from
      its definition's right-hand side,
    * its **compute level**: `inline` (the default), `root`, or `at(host,
      var)`. This is the only piece of state the bootstrap schedule directives
      change.

* **`ImageParam`** — an input buffer. It is a *leaf*: it is never computed and
  never appears in the loop nest. A Func that reads an `ImageParam` simply has
  one fewer producer to realize; the buffer is assumed already present.

* **`Expr`** — a value expression appearing on the right-hand side of a
  definition. For loop-nest purposes the only thing that matters about an Expr
  is *which Funcs it reads*: that is what wires up the producer/consumer graph.
  Pointwise arithmetic, constants, `cast<T>(...)`, and the like are invisible
  in the loop nest — they live *inside* the `f(...) = ...` leaf line.
  Exception: 1-iteration loops are removed; this relies on bounds inference,
  which depends more deeply on the contents of an Expr. See §7.

The set of Funcs, connected by producer edges, forms a directed acyclic graph
(the *pipeline*). One Func is the **output**: the one whose
`print_loop_nest()` (or `realize`) you call. Everything reachable from the
output by following producer edges is part of the pipeline; nothing else is.

See [examples/two_funcs_root.cpp](examples/two_funcs_root.cpp) for the smallest
two-stage pipeline.

---

## 2. Reading a loop nest

`print_loop_nest()` prints pseudocode with five line shapes, nested by 2-space
indentation:

```
produce <f>:        # contains a region computing (storing into) Func f
consume <f>:        # contains a region that reads f's stored values
for <var>:          # a loop over one dimension
<f>(...) = ...      # the leaf: store one point of f (arguments and RHS elided)
store <f>:          # f's storage scope, shown only when it differs from compute (§8)
```

Indentation is containment. `produce f` contains the loops that compute `f` and
ends at the matching `consume`/dedent; everything that reads `f` is nested
under `consume f`. A `for` contains its loop body. The leaf `f(...) = ...` is
the body executed at the innermost point.

A consequence worth stating up front: when several Funcs are produced in
sequence, their `consume` blocks *nest* — the rest of the program, **including
any later producers**, sits inside the current `consume` rather than appearing
as a flat list of siblings. How deeply each producer's block nests is set by its
realization order and its compute level (§4, §7), so two producers of the same
Func need not appear at the same depth.

Two cosmetic details are *not* part of the model and are normalized away by the
test harness (`../canonicalize.py`): the exact loop-variable names, and constant
loop bounds (`for x in [0, 7]`). What *is* significant: the produce/consume
nesting, the number and nesting of `for` loops, their order, and (in later
revisions) their type (`parallel`, `vectorized`, …).

---

## 3. The default schedule: inlining

By default every Func **except the output is inlined**. An inlined Func has no
loops and no `produce`/`consume` of its own: wherever a consumer reads it, the
inlined Func's definition is substituted in, as if textually pasted. It simply
*disappears* from the loop nest.

So a pipeline of pure Funcs with no scheduling at all collapses to a single
loop nest over the output's dimensions, with every producer folded into the
output's leaf. See [examples/inline_default.cpp](examples/inline_default.cpp):
`producer` is read twice by `consumer` but never appears; only `consume`'s
loops are emitted.

Inlining trades memory for redundant computation: each use re-evaluates the
producer (here `producer` is effectively computed twice per output pixel). The
output Func itself is never inlined — it is always realized at the root (§4).

---

## 4. `compute_root`: realize once at the top

`f.compute_root()` sets `f`'s compute level to `root`: `f` is computed in full,
once, at the outermost level, *before* anything that uses it. It gets its own
loop nest (§5) wrapped in `produce f`, and the rest of the program is nested
under `consume f`. Note that `consume f` is **not** selective: as in §2, it
mechanically wraps everything emitted after `produce f` — typically the entire
remainder of the pipeline — regardless of which parts actually read `f`. (The
name reflects that `f`'s values are now available to be consumed there, not that
the wrapped code is exactly `f`'s readers.)

When several Funcs are `compute_root` (plus the output, which is always at
root), they are emitted in **realization order**: a topological order of the
pipeline graph in which every producer precedes its consumers. Each non-final
realization wraps the rest of the program in its `consume` block. Concretely,
for root-level Funcs `F1, F2, …, Fn` in realization order:

```
produce F1:
  <loops of F1>
consume F1:
  produce F2:
    <loops of F2>
  consume F2:
    ...
        produce Fn:
          <loops of Fn>
```

The final Func (always the output) has **no `consume`** block, because nothing
reads it. Every earlier root Func has exactly one `consume` wrapping all the
realizations that come after it.

* [examples/two_funcs_root.cpp](examples/two_funcs_root.cpp): one
  `compute_root` producer feeding the output.
* [examples/diamond_root.cpp](examples/diamond_root.cpp): a shared producer
  feeding two intermediates that both feed the output. Note the producer is
  realized exactly **once** even though two Funcs read it — realization order
  visits each Func once, producers first.
* [examples/box_blur.cpp](examples/box_blur.cpp): a four-stage blur where two
  stages are `compute_root` and the rest are inlined. The inlined stages
  (`blur_x`, `blur_y`, and the anonymous `cast` wrappers) vanish; only the two
  rooted stages and the output appear.

### Realization order in detail

Realization order is a topological sort of the *whole* graph (including inlined
Funcs), then restricted to the realized Funcs. Inlined Funcs do not get their
own slot, but they still transmit dependencies: if inlined `b` reads rooted
`a`, then any Func that inlines `b` depends (transitively) on `a`, so `a`
precedes it. In `box_blur`, `output` inlines `blur_y`→`blur_x`→`input_16`
(rooted), so `input_16` is realized before `output`.

#### Tie-break: which sibling producer goes first

A topological sort leaves freedom whenever a consumer reads two producers that
do not depend on each other — e.g. `output(...) = g(...) + h(...)` with both `g`
and `h` at root. Halide breaks the tie **deterministically by name, not by the
order they appear in the defining expression**:

> Sibling producers are ordered by **name prefix** (alphabetically), then by
> **first-visitation order** (the order they are first reached walking the
> pipeline from the output), then by full name. The "prefix" is the name with
> any `$n` uniqueness suffix and any trailing digits removed.

So in [examples/tiebreak_realization_order.cpp](examples/tiebreak_realization_order.cpp),
even though the expression is written `b1d(x) + a2d(x, y)`, `a2d` is realized
first because `"a2d" < "b1d"`. The left-to-right order of `+` is irrelevant. The
first-visitation tie-break only matters when two prefixes are equal (e.g.
auto-named Funcs sharing a prefix); examples here use distinct prefixes so the
order is purely alphabetical. This same ordering decides the order of sibling
producers filed at any single `compute_at` level, not just root (§7).

---

## 5. A single Func's own loops

Inside `produce f`, the Func is computed by a loop nest over its pure
dimensions. The rule for loop order:

> **The first argument of the definition is the innermost loop; the last is the
> outermost.**

So `f(x, y, c) = ...` produces, from outside in, `for c: for y: for x:` with
the leaf `f(...) = ...` at the center. This is row-major traversal: the first
dimension varies fastest. (`reorder`, a later topic, is what changes this.)

```
produce f:
  for c:
    for y:
      for x:
        f(...) = ...
```

The number of `for` loops at a root realization equals the Func's number of
pure dimensions. (For `compute_at`, see the caveat in §7.)

---

## 6. Function names and identity in the output

Halide prints each Func's name, but the test harness replaces names with
positional ids (`F0`, `F1`, … in order of first appearance), so you only need
to get the *structure* and the *number of distinct Funcs* right, not reproduce
Halide's exact names. This matters because some Funcs are auto-named:

* `BoundaryConditions::repeat_edge(input)` and friends create a wrapper Func
  (printed `repeat_edge`) whose dimensions are auto-named `_0, _1, _2, …`. It
  is a real Func and is realized if scheduled (in `box_blur` it is
  `compute_root`). Its dimension count matches the input's.
* `cast<T>(...)` does **not** create a Func — it is an Expr operation that
  stays inside the leaf line of whatever definition contains it.

---

## 7. `compute_at`: realize inside a consumer's loop

`f.compute_at(g, var)` sets `f`'s level to `at(g, var)`: `f` is realized
*inside* `g`'s loop over `var`, recomputed on each iteration of that loop, just
before the part of `g`'s body that uses it. Its `produce`/`consume` block is
injected as a prefix to that loop level's body; the rest of `g`'s body (the
deeper loops and eventually `g`'s leaf) becomes the content of `f`'s `consume`.

For `producer.compute_at(consumer, y)` where `consumer(x, y) = producer(x, y) +
producer(x, y + 1)` (see [examples/compute_at.cpp](examples/compute_at.cpp)):

```
produce consumer:
  for y:
    produce producer:
      for y:
        for x:
          producer(...) = ...
    consume producer:
      for x:
        consumer(...) = ...
```

The injection is recursive: a Func computed inside a Func that is itself
computed inside another nests accordingly, because each Func's loops are
generated the same way and children are injected at each of *its* loop levels.

### Multiple producers of one consumer

When a consumer reads several producers, each producer is placed according to
*its own* compute level; they do not share a single flat `consume` block.

* **Both at the same level** (e.g. both `compute_at(output, y)`, or both at
  root): they form a nested produce/consume chain at that level, ordered by the
  realization-order tie-break (§4) — alphabetical by name, *not* expression
  order. The first producer's `consume` wraps the second producer, whose
  `consume` wraps the rest. (Root is just the special case where the "level" is
  the outermost one; see [examples/diamond_root.cpp](examples/diamond_root.cpp).)

* **At different levels.** Each producer is injected at its own loop level of the
  consumer, so the producer at the *outer* level appears first and its `consume`
  contains both the consumer's inner loops *and* the inner producer's block. In
  [examples/producers_diff_levels.cpp](examples/producers_diff_levels.cpp),
  `h.compute_at(output, y)` (outer) and `g.compute_at(output, x)` (inner) give:

  ```
  produce output:
    for y:
      produce h:
        ...
      consume h:
        for x:
          produce g:
            ...
          consume g:
            output(...) = ...
  ```

  `g` is nested *inside* `consume h` not because `g` depends on `h` (it does
  not), but because `g`'s loop level (`x`) is inside `h`'s (`y`). Compute level,
  not the producer/consumer graph, determines this nesting.

* **One at root, one `compute_at`.** The root producer joins the top-level chain
  (§4) and never nests inside the consumer; the `compute_at` producer nests
  inside the consumer's loops as usual. See
  [examples/producers_root_and_at.cpp](examples/producers_root_and_at.cpp).

### Loop elision: a `compute_at` Func may emit fewer loops than its dimensions

A root Func always emits one loop per dimension (§5). A `compute_at` Func does
**not**, in general. Halide computes only the *region* of the producer needed
per iteration of the host, and a dimension whose needed extent is a single
point becomes an extent-1 loop that Halide **simplifies away entirely** — no
`for` line is printed for it. Conceptually:

> A dimension `d` of `f.compute_at(g, L)` survives as a loop iff the values of
> `f`'s `d`-coordinate read by `g` span more than one point as `g`'s loops
> *inner to `L`* run. Reading `f` at a multi-tap stencil in `d`, or at an index
> that varies with an inner host loop, keeps the loop; a single-point read
> collapses it.

Worked cases, all `f.compute_at(output, x)` with `output` reading `f` at offsets
`(0,0), (dx,0), (0,dy), (dx,dy)`:

* dx = dy = 1: both loops survive — [examples/loop_elide_test.cpp](examples/loop_elide_test.cpp).
* dx = 0: `f`'s `x` is read at one point → `x` loop elided — [examples/loop_elide_x.cpp](examples/loop_elide_x.cpp).
* dy = 0: `y` loop elided — [examples/loop_elide_y.cpp](examples/loop_elide_y.cpp).
* dx = dy = 0: both elided, `f` emits no loops at all (`produce f: f(...) =
  ...`) — [examples/loop_elide_both.cpp](examples/loop_elide_both.cpp). This is
  why `compute_at` at the innermost loop of a pointwise consumer behaves almost
  like inlining.

**Predicting exactly which dimensions collapse requires bounds inference**,
which is undecidable in general and out of scope for this document. We therefore
*declare* elision rather than derive it: an example annotates it with

    f.compute_at(output, x);
    micro_halide_collapses(f, {x});   // f's x loop has extent 1 here and is elided

`micro_halide_collapses(f, {vars...})` is a no-op under real Halide; it tells micro_halide
which loops to drop. The split between *structure* (taught here, derived from
the schedule) and *elision* (declared) is described in the README. The loop
*structure* — produce/consume placement, ordering, and the surviving loops — is
fully determined by the schedule as described in §§4–9.

### An elided loop is still a `compute_at` injection site

Eliding a loop only removes its `for` line; the loop's *position* in the nest is
preserved. So a Func computed at an elided loop is still injected there, as a
prefix of the host's body, outside any surviving inner loops of the host. See
[examples/compute_at_elided_level.cpp](examples/compute_at_elided_level.cpp):
`h.compute_at(output, x)` elides `h`'s `y` loop, yet `p.compute_at(h, y)` still
places `p` at that (loop-less) level:

```
produce h:
  produce p:
    for y:
      for x:
        p(...) = ...
  consume p:
    for x:           # h's surviving x loop
      h(...) = ...
```

### When a `compute_at` is illegal

`f.compute_at(g, v)` is not always legal. Halide computes the set of **legal
compute sites** for `f` as the loop levels that **enclose every use of `f`** —
formally, the intersection of the loop-level stacks at all of `f`'s call sites,
plus `root`. If the requested site is not in that set, Halide rejects the
schedule with *"Func f is computed at the following invalid location"* and lists
the legal ones; no loop nest is produced. Three ways to land outside the legal
set, all with only the features so far:

* **The loop does not exist.** `v` must be one of `g`'s pure dimensions. Naming
  a `Var` that `g` never loops over has no site to inject into —
  [examples/neg_compute_at_bad_var.cpp](examples/neg_compute_at_bad_var.cpp).
* **The host is not a consumer.** `g` must actually read `f` (directly, or
  through Funcs inlined into it); otherwise `g` has no point that needs `f`, and
  `f`'s real consumer lies outside `g` —
  [examples/neg_compute_at_nonconsumer.cpp](examples/neg_compute_at_nonconsumer.cpp).
* **A consumer lies outside the chosen site.** If `f` is read in more than one
  place, the site must enclose *all* of them. Computing `f` inside one consumer
  when another consumer (e.g. the output at root) also needs it leaves that
  other read with no values. When `f` is used at two unrelated places the only
  common enclosing site is `root` —
  [examples/neg_compute_at_two_consumers.cpp](examples/neg_compute_at_two_consumers.cpp).

The last case is the fundamental one: a Func computed at a single site can only
feed consumers within that site. Feeding consumers that live at different,
non-nested locations is exactly what the wrapper Funcs `in()` / `clone_in()` (a
later milestone) exist to enable; until then, such a schedule is simply illegal.

These are *negative* examples: both Halide and `micro_halide` must reject them
(exit with an error) rather than print a loop nest. `micro_halide` validates the
same rule — host realized, loop exists, and every reader of `f` enclosed by the
site — before emitting.

---

## 8. `store_at` / `store_root`: storage level vs. compute level

So far a Func has had a single *compute level* (§4, §7) that fixes both where it
is computed and where its storage is allocated. These can be separated. Besides
its compute level, a Func has a **store level**: the loop at which its buffer is
allocated. By default the store level **equals** the compute level. Two
directives change it (and *only* it — they do not move the computation):

* `f.store_at(g, v)` — allocate `f`'s storage in host `g`'s loop over `v`.
* `f.store_root()` — allocate `f`'s storage at the outermost level.

The point of separating them is to allocate storage at an *outer* loop while
computing at an *inner* loop: values computed on one iteration of the inner loop
can then be reused on later iterations (Halide's *sliding window* optimization),
and the buffer can be folded down to a small size. Those are changes to *which
values are recomputed* and to *buffer sizes* — they do **not** change the
produce/consume/`for` structure that `print_loop_nest` prints (buffer sizes only
affect constant bounds, which the harness ignores). The single visible effect on
the loop nest is an added `store` node.

### The `store` node

`print_loop_nest` prints `store f:` **only when `f`'s store level differs from
its compute level.** When they are equal — the default, and also
`store_root().compute_root()` (both at root) — there is no `store` line at all.

When shown, the `store f:` node sits at the **store level** and contains
everything from there down to `f`'s `produce`/`consume` at the compute level. The
`produce`/`consume` of `f` and every `for` loop stay exactly where `compute_at`
alone (§7) would place them; `store_at` only adds the enclosing `store f:` line
(and the host loops between the store level and the compute level fall inside
it).

For `g.store_at(f, y).compute_at(f, x)` — store at the outer loop `y`, compute at
the inner loop `x` (see [examples/store_at_compute_at.cpp](examples/store_at_compute_at.cpp)):

```
produce f:
  for y:
    store g:          # at the store level (f's y)
      for x:          # host loop between store and compute level
        produce g:    # at the compute level (f's x)
          for y:
            for x:
              g(...) = ...
        consume g:
          f(...) = ...
```

`store_root()` puts the `store` node at the **outermost** level — outside even
the output's `produce`, wrapping the entire pipeline body. For
`g.store_root().compute_at(f, y)` with `f` the output (see
[examples/store_root_compute_at.cpp](examples/store_root_compute_at.cpp)):

```
store g:              # outermost: storage for the whole pipeline body
  produce f:
    for y:
      produce g: ...
      consume g:
        for x:
          f(...) = ...
```

When the host of the compute level is itself an intermediate Func, the `store`
node still lands at the named store loop and wraps that Func's whole realization
(its `produce` *and* `consume`); see
[examples/store_root_chain.cpp](examples/store_root_chain.cpp).

### Legality

* The store level must **enclose** the compute level — it must be the same loop
  or an *outer* one. Storing inside the compute loop is illegal
  ([examples/neg_store_inside_compute.cpp](examples/neg_store_inside_compute.cpp)).
* A Func with a store level must also have a non-inline compute level: using
  `store_at`/`store_root` without `compute_at`/`compute_root` is illegal
  ([examples/neg_store_at_inlined.cpp](examples/neg_store_at_inlined.cpp)).
* Like the compute level, the store level must enclose every use of `f` (§7's
  legal-site rule applies to it too).

### `hoist_storage` / `hoist_storage_root`: no effect on the printed nest

There is a third, even more physical level: the **hoist-storage level**, set by
`f.hoist_storage(g, v)` or `f.hoist_storage_root()`. It moves the actual memory
*allocation* further out (to avoid re-allocating inside a loop) **without**
triggering the sliding-window reuse that `store_at` enables. By default it
coincides with the store level.

For `print_loop_nest` this directive is **invisible**: it changes neither the
`produce`/`consume`/`store`/`for` structure nor the loop order. A schedule with
`hoist_storage` prints exactly the same nest as the same schedule without it
([examples/hoist_storage_noop.cpp](examples/hoist_storage_noop.cpp) prints
identically to plain `compute_at`). It only affects allocation placement and
buffer sizing, which the loop nest does not display.

The one way `hoist_storage` shows up is by making an otherwise-fine schedule
**illegal**:

* Like `store_at`, it requires a non-inline compute level — `hoist_storage` /
  `hoist_storage_root` on an inlined Func is illegal
  ([examples/neg_hoist_at_inlined.cpp](examples/neg_hoist_at_inlined.cpp)).
* The hoist-storage level must **enclose the store level** (which encloses the
  compute level): allocation cannot live inside the loop whose iterations reuse
  it. Hoisting to a loop inside the compute level is illegal
  ([examples/neg_hoist_inside_compute.cpp](examples/neg_hoist_inside_compute.cpp)).

So: `hoist_storage` is a no-op for the structure this document teaches, except
that it adds these two legality constraints.

---

## 9. Putting the algorithm together (how the nest is built)

The whole loop nest follows from the rules above, assembled into one procedure:

1. **Force the output to `root`** — the Func you call `print_loop_nest()` on is
   always computed at the outermost level (§3, §4).

2. **Compute the realization order** — topologically sort the pipeline so every
   producer precedes its consumers, breaking ties by name (§4). Inlined Funcs
   remain in this order so they can pass dependencies along, but they are never
   realized and so drop out of the steps below (§3).

3. **Give every realized Func a site.** A *realized* Func is one that is not
   inlined: the output, plus anything scheduled `compute_root` or `compute_at`.
   Each goes to exactly one place:
     * `compute_root` Funcs and the output form the **top-level chain**, kept in
       realization order;
     * a `compute_at(g, v)` Func is **filed under** host `g`'s loop over `v`. If
       several Funcs are filed at the same `(g, v)`, they keep realization order.
   Each realized Func also has a **store level** (§8), defaulting to its compute
   level; remember it for step 4.

4. **Emit from the outside in.** Walk the top-level chain (§4): for each Func
   print `produce f`, then `f`'s loop nest, then — for every Func but the last —
   `consume f` wrapping everything that follows. Printing a Func's loop nest
   (§5) means working from its outermost dimension inward, and at each dimension:
     * if that dimension was declared elided (§7), skip its `for` line but still
       treat the level as a valid injection site;
     * if this `(f, dim)` level is the **store level** of some Func `h` whose
       compute level is deeper (§8), open an `h`'s `store h:` node here first;
       everything emitted below at this level — the intervening loops and `h`'s
       own `produce`/`consume` further in — falls inside that `store h:`;
     * inject the Funcs filed at this `(f, dim)` level (from step 3) — each as a
       `produce`/`consume` pair whose `consume` wraps the rest of `f`'s body;
     * descend to the next-inner dimension, bottoming out at the leaf
       `f(...) = ...`.
   Injection is recursive: an injected Func's own loop nest is emitted the same
   way, so a Func filed inside a Func that is itself filed inside a third nests
   accordingly (§7). A Func scheduled `store_root()` is the special case where
   its `store` node opens at the very outermost level, wrapping the whole nest
   (it has no `for`-level host to attach to).

[examples/many_compute_root.cpp](examples/many_compute_root.cpp) puts the
core pieces together: `f1`, `f2`, `f3` are `compute_root` and so form the
top-level chain in that order; `f4` is `compute_at(output, y)` and is injected
under the output's `y` loop; and `clamped` is inlined, so it never appears — it
is folded into `f1`'s leaf.
[examples/many_store_at.cpp](examples/many_store_at.cpp) extends it to exercise
the storage level: a `store_root` Func, a `store_at`-at-an-outer-loop Func
(distinct `store`/compute levels), and a Func whose store level equals its
compute level (no `store` node).

---

## Source-level evidence

The compiler-level justification for the above — where outputs are forced to
`compute_root`, where the produce/consume nodes and realization order come
from, and why extent-1 loops disappear — is in
[src_doc/loop_nest_construction.md](src_doc/loop_nest_construction.md).
