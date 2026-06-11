# Halide scheduling and the loop nest

This document explains how Halide turns a *scheduled pipeline* into a *loop
nest*, at the level of detail needed to predict the output of
`Func::print_loop_nest()` by hand. It is built up holistically: each section
adds to a single mental model rather than describing an isolated feature.

> Scope of this revision: the programming model, pure Funcs, the default
> (inline) schedule, `compute_root`, `compute_at`, `store_at` / `store_root`,
> `hoist_storage` / `hoist_storage_root`, the loop transforms `split` / `fuse` /
> `reorder` / `tile`, **update (reduction) definitions** with `RDom` / `RVar`,
> and the `print_loop_nest()` output format. Wrappers (`in`/`clone_in`),
> `rfactor`, loop-type directives (`parallel`/`vectorize`/`unroll`), and GPU
> scheduling are deferred to later revisions. Where one of those interacts with
> the model below in a way you can already observe, it is flagged explicitly.

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
    * its **name** (used for printing; see §7,
      and computation order tie-breaking, see §4),
    * its ordered list of **pure dimensions** (the `Var`s on the left-hand
      side of its *initial* definition; the first listed is the *innermost*
      loop — see §5),
    * its ordered list of **stages**: an initial (pure) definition plus zero or
      more **update definitions** (§10). A Func with no updates has just one
      stage; each stage gets its own loops and its own per-stage schedule.
    * the set of **other Funcs it reads from** (its *producers*), derived from
      the right-hand sides (and update left-hand-side indices) of all its
      stages,
    * its **compute level** and **store level**: `inline` (the default),
      `root`, or `at(host, var)`. These apply to the Func as a whole (all
      stages move together).

* **`ImageParam`** — an input buffer. It is a *leaf*: it is never computed and
  never appears in the loop nest. A Func that reads an `ImageParam` simply has
  one fewer producer to realize; the buffer is assumed already present.

* **`Expr`** — a value expression appearing on the right-hand side of a
  definition. For loop-nest purposes the only thing that matters about an Expr
  is *which Funcs it reads*: that is what wires up the producer/consumer graph.
  Pointwise arithmetic, constants, `cast<T>(...)`, and the like are invisible
  in the loop nest — they live *inside* the `f(...) = ...` leaf line.
  Exception: 1-iteration loops are removed; this relies on bounds inference,
  which depends more deeply on the contents of an Expr. See §8.

* **`RDom` / `RVar`** — a *reduction domain* and its *reduction variables*. An
  `RDom r(min, extent, …)` declares one or more `RVar`s (`r.x`, `r.y`, …, or
  just `r` for a 1-D domain) used in **update definitions** (§10). An `RVar`
  names a loop just like a `Var`, but the loop iterates the declared reduction
  range and is *ordered* (unlike a pure `Var`, its iterations may carry a
  dependency). RVars only appear in update definitions and produce extra loops
  there.

The set of Funcs, connected by producer edges, forms a directed acyclic graph
(the *pipeline*). One Func is the **output**: the one whose
`print_loop_nest()` (or `realize`) you call. Everything reachable from the
output by following producer edges is part of the pipeline; nothing else is.

See [examples/two_funcs_root.cpp](examples/two_funcs_root.cpp) for the smallest
two-Func pipeline.

---

## 2. Reading a loop nest

`print_loop_nest()` prints pseudocode with five line shapes, nested by 2-space
indentation:

```
produce <f>:        # contains a region computing (storing into) Func f
consume <f>:        # contains a region that reads f's stored values
for <var>:          # a loop over one dimension
<f>(...) = ...      # the leaf: store one point of f (arguments and RHS elided)
store <f>:          # f's storage scope, shown only when it differs from compute (§9)
```

Indentation is containment. `produce f` contains the loops that compute `f` and
ends at the matching `consume`/dedent; everything that reads `f` is nested
under `consume f`. A `for` contains its loop body. The leaf `f(...) = ...` is
the body executed at the innermost point.

A consequence worth stating up front: when several Funcs are produced in
sequence, their `consume` blocks *nest* — the rest of the program, **including
any later producers**, sits inside the current `consume` rather than appearing
as a flat list of siblings. How deeply each producer's block nests is set by its
realization order and its compute level (§4, §8), so two producers of the same
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
producers filed at any single `compute_at` level, not just root (§8).

---

## 5. A single Func's own loops

Inside `produce f`, the Func is computed by a loop nest over an ordered list of
**loop dimensions**. Make that list explicit, because the loop transforms in §6
rewrite it:

> Each Func (more precisely, each definition) carries an ordered **dimension
> list**, *innermost first*. It starts as the pure arguments in definition
> order — the first argument is the innermost dimension — with an implicit
> `outermost` sentinel pinned at the end (which you can ignore). The Func emits
> **one `for` loop per dimension**, printed *outermost first* (the reverse of
> the list), with the leaf `f(...) = ...` at the center.

So `f(x, y, c) = ...` has dimension list `[x, y, c]` and produces, from outside
in, `for c: for y: for x:`. This is row-major traversal: the first dimension
varies fastest. (`reorder`, §6, is what changes this order.)

(Strictly, the dimension list belongs to a *stage*. A Func with only a pure
definition has one stage, described here. A Func with update definitions has
several stages, each with its own dimension list — possibly including reduction
loops — emitted one after another inside the single `produce f`; see §10.)

```
produce f:
  for c:
    for y:
      for x:
        f(...) = ...
```

The number of `for` loops at a root realization equals the length of the
Func's dimension list — its argument count by default, but adjusted by the
transforms in §6. (For `compute_at`, see the caveat in §8.)

---

## 6. Reshaping a Func's loops: `split`, `fuse`, `reorder`, `tile`

These four directives rewrite a Func's **dimension list** (§5) — they add,
remove, rename, and reorder its loops. They change *only this Func's own loops*
(and the dimension names you may later use as `compute_at`/`store_at` sites);
they never move the Func relative to other Funcs and never change which values
are computed. A `compute_at`/`store_at` may name any dimension *currently* in
the list, including ones these transforms created.

### `split`

`f.split(old, outer, inner, factor)` replaces dimension `old` with two
dimensions: `inner` (innermost, iterating `0 .. factor-1`) and `outer` just
outside it. The dimension list `[x, y]` under `split(x, xo, xi, 8)` becomes
`[xi, xo, y]`, printing `for y: for xo: for xi:`. Net effect: **one extra `for`
loop** at `old`'s position. It is fine to reuse `old`'s name as `inner` or
`outer`. See [examples/split_basic.cpp](examples/split_basic.cpp).

Halide prints the new vars with dotted names (`x.xo`, `x.xi`) and gives the
inner loop the constant bound `[0, factor-1]`, but the harness normalizes both
loop names and constant bounds away — so the only structural signal of a
`split` is the added loop.

### `fuse`

`f.fuse(inner, outer, fused)` is the inverse: it removes the `inner` and `outer`
dimensions and puts a single `fused` dimension at `inner`'s former position,
iterating over the product of their extents. `[x, y]` under `fuse(x, y, xy)`
becomes `[xy]`, printing one loop `for xy:`. Net effect: **one fewer `for`
loop**. See [examples/fuse_basic.cpp](examples/fuse_basic.cpp).

### `reorder`

`f.reorder(v_inner, …, v_outer)` lists dimensions *innermost first* and permutes
**only the listed dimensions among the slots they currently occupy** — any
dimension you don't name keeps its position. So `f(x, y)` (list `[x, y]`, loops
`for y: for x:`) under `reorder(y, x)` becomes list `[y, x]`, loops
`for x: for y:`.

> **`reorder` of plain serial loops is invisible to `print_loop_nest` as this
> document models it.** Because the harness normalizes away loop-variable names
> and constant bounds, swapping the order of two ordinary `for` loops produces
> structurally identical output — the same count and nesting of untyped `for`s.
> The inner/outer order chosen by a `split` (or `tile`) is invisible for the
> same reason.

`reorder` becomes observable only through a **topological consequence**: it
changes *which loop a `compute_at` producer sits under*, and therefore how many
host loops fall inside that producer's block.
[examples/reorder_topological.cpp](examples/reorder_topological.cpp) reorders a
consumer's dimensions so the producer's `compute_at` site moves to the innermost
loop; contrast [examples/reorder_baseline.cpp](examples/reorder_baseline.cpp),
the same pipeline without the `reorder`, where the producer sits one level out
with a surviving inner loop inside its block. (`reorder` also becomes directly
visible once loops carry distinct *types* — `vectorize`/`parallel`/`unroll`, a
later milestone — which the harness *does* keep.)

### `tile`

`f.tile(x, y, xo, yo, xi, yi, xf, yf)` is shorthand for splitting both `x` and
`y` and reordering the four results into a tiled traversal. It is exactly:

```
f.split(x, xo, xi, xf);
f.split(y, yo, yi, yf);
f.reorder(xi, yi, xo, yo);   // innermost first
```

giving dimension list `[xi, yi, xo, yo]` and loops `for yo: for xo: for yi: for
xi:`. Net effect: **two extra `for` loops**, in tiled order. See
[examples/tile_basic.cpp](examples/tile_basic.cpp).

### Transformed dimensions are `compute_at` / `store_at` sites

The dimensions these transforms produce are first-class loop levels. A producer
filed at host dimension `d` is injected just inside `d`'s loop, with the host
loops *inner* to `d` (those earlier in the list) falling inside the producer's
`consume` — the same rule as §8, now applied to the *post-transform* list. In
[examples/split_compute_at.cpp](examples/split_compute_at.cpp), a producer is
`compute_at` the consumer's split *outer* loop and so lands between the outer
and inner loops of the split.

### Legality

* `split`, `fuse`, and `reorder` must name dimensions that *currently* exist. A
  var that was never a dimension, or one that a previous `fuse` already consumed,
  is rejected. Reordering over a non-dimension
  ([examples/neg_reorder_bad_var.cpp](examples/neg_reorder_bad_var.cpp)) errors,
  and `compute_at` at a dimension a `fuse` removed
  ([examples/neg_compute_at_fused_away.cpp](examples/neg_compute_at_fused_away.cpp))
  is just the §8 "site must be a current loop" rule applied after a transform —
  only the fused var remains a legal site.
* `reorder` must reference each dimension at most once.

---

## 7. Function names and identity in the output

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

## 8. `compute_at`: realize inside a consumer's loop

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
fully determined by the schedule as described in §§4–11.

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

## 9. `store_at` / `store_root`: storage level vs. compute level

So far a Func has had a single *compute level* (§4, §8) that fixes both where it
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
alone (§8) would place them; `store_at` only adds the enclosing `store f:` line
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
* Like the compute level, the store level must enclose every use of `f` (§8's
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

## 10. Update (reduction) definitions

So far each Func has had a single definition. A Func may also have **update
definitions**: extra assignments, written after the initial one, that *modify*
the Func's values in place. The initial definition plus the updates form an
ordered list of **stages**.

```cpp
Func hist("hist");
hist(x) = 0;              // stage 0: the pure / initial definition
RDom r(0, N, "r");
hist(in(r)) += 1;         // stage 1: an update definition
```

Each stage runs to completion before the next begins, and they all write to the
**same** storage. A Func with `k` update definitions has `k + 1` stages,
numbered `s0` (pure), `s1`, …, `sk` in definition order.

### Stages share one `produce`

All of a Func's stages are emitted **inside the single `produce f` block**, as
consecutive sibling loop nests — *not* as separate `produce`/`consume` blocks.
There is no `consume` between stages; a consumer's `consume f` (if any) wraps the
whole thing, because a reader sees the Func's *final*, post-update values. For
the histogram above (see [examples/hist_1d.cpp](examples/hist_1d.cpp)):

```
produce hist:
  for x:              # stage 0: initialise hist(x) = 0
    hist(...) = ...
  for r:              # stage 1: the scatter update
    hist(...) = ...
```

### A stage's loops: free `Var`s plus `RVar`s

Each stage has its **own dimension list** (§5), built from its own
left-hand side:

> An update stage loops over the **free `Var`s** appearing on its left-hand
> side **plus the `RVar`s** of any `RDom` it uses. Default order, innermost
> first: the `RVar`s are innermost — and *within* the `RVar`s the first-declared
> dimension (`r.x`) is the **innermost** loop, matching the `Var` convention
> that the first dimension varies fastest (§5) — and the free pure `Var`s sit
> outside them (in the usual order, first LHS argument innermost among the
> pures). A pure dimension whose left-hand-side slot is occupied by an `RVar` or
> a general expression (e.g. the `in(r)` index in the histogram, or `f(x, r)`)
> does **not** produce a loop in that stage.

`RVar` loops print like any other (`for r in [min, max]`); the harness drops the
constant bound, so they read as plain `for`. A `k`-dimensional `RDom` contributes
`k` nested reduction loops. So `f(x, y) += in(x + r.x, y + r.y)` with a 2-D
`RDom` gives the update stage loops `for y: for x: for r.y: for r.x:` (`r.x`
innermost; [examples/update_2d_rdom.cpp](examples/update_2d_rdom.cpp)), while the pure
stage `f(x, y) = 0` just gives `for y: for x:`. A reduction with no free
variable on the left, like the histogram scatter, gives a stage with only the
reduction loop(s).

* [examples/sum_reduction.cpp](examples/sum_reduction.cpp): `f(x) = 0; f(x) +=
  in(x, r)` — the pure stage loops over `x`, the update stage over `x` then the
  reduction `r`.
* [examples/two_updates.cpp](examples/two_updates.cpp): three stages (`s0`, `s1`,
  `s2`) printed in order inside one `produce`.

### Scheduling stages independently

Every stage carries its own schedule, so the loop transforms of §6 (and, later,
loop types) apply **per stage**:

* `f.<directive>(…)` schedules the **pure** stage (`s0`).
* `f.update(i).<directive>(…)` schedules update stage `s(i+1)` (so
  `f.update(0)` is the first update).

A `split`/`reorder`/`fuse`/`tile` on one stage rewrites only *that stage's*
dimension list and leaves the others alone — and these transforms treat `RVar`s
just like `Var`s in the list. In [examples/update_stage_split.cpp](examples/update_stage_split.cpp)
only the update stage's `x` is split; the pure stage is untouched.
[examples/update_stage_reorder.cpp](examples/update_stage_reorder.cpp) reorders a
free `Var` inside the reduction loop of the update stage only.

(One legality note that this document does **not** model mechanically: an `RVar`
loop is *ordered*, so reordering two `RVar`s — or otherwise reshuffling them past
each other — is rejected unless the reduction is associative *and* commutative,
which Halide decides by analysing the update's arithmetic. Predicting that needs
the actual expression semantics, which is out of scope here, just like bounds
inference for loop elision in §8.)

### Compute / store level, and `RVar`s as injection sites

A Func's compute and store levels (§§7–9) apply to the Func as a **whole**: the
entire `produce f` — all its stages — is realized together at the chosen site.
Computing a Func with updates inside a consumer therefore drops the whole
multi-stage block at that loop level
([examples/func_update_compute_at.cpp](examples/func_update_compute_at.cpp)).

Going the other way, a stage's loops — **including its `RVar` loops** — are
valid sites at which to inject *other* Funcs. A producer read inside a reduction
can be `compute_at` that reduction loop, and it is placed inside that specific
stage's loop ([examples/producer_at_rvar.cpp](examples/producer_at_rvar.cpp)):

```
produce f:
  for x:                      # stage 0
    f(...) = ...
  for x:                      # stage 1
    for r:
      produce p:              # p computed inside the reduction loop
        p(...) = ...
      consume p:
        f(...) = ...
```

When the compute site is a loop that appears in **several** stages of the host
(typically a pure `Var` that survives in more than one stage), the producer is
injected **once into each such stage**, serving that stage's reads. The stages
are sibling loop nests (§10), so each gets its own copy of the producer's
`produce`/`consume`. In [examples/cross_stage_compute_at_shared.cpp](examples/cross_stage_compute_at_shared.cpp),
`p` is read by both of `f`'s stages and computed at the shared `x` loop, so a
`produce p`/`consume p` appears inside *both* stages' `x` loops.

The legality rule of §8 carries over, now spanning **all** stages: a producer's
compute site must be a loop present in **every** stage that reads the producer
(so each reading stage can host an injection). An `RVar` loop exists only within
its own stage, so a producer that is also read by another stage cannot be
computed there — its only common sites are loops shared by all the using stages
(and `root`). In
[examples/neg_compute_at_update_rvar.cpp](examples/neg_compute_at_update_rvar.cpp),
`p` is read by both the pure stage and the update stage, so computing it at the
update's reduction loop is illegal.

---

## 11. Putting the algorithm together (how the nest is built)

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
   Each realized Func also has a **store level** (§9), defaulting to its compute
   level; remember it for step 4. The `compute_at`/`store_at` site `v` is a
   dimension of the host's **(possibly transformed) dimension list** (§5, §6):
   `split`/`fuse`/`reorder`/`tile` have already rewritten that list before this
   step, so `v` is matched against the post-transform dimensions.

4. **Emit from the outside in.** Walk the top-level chain (§4): for each Func
   print `produce f`, then `f`'s loop nest(s), then — for every Func but the
   last — `consume f` wrapping everything that follows. A Func with update
   definitions (§10) emits **one loop nest per stage**, in stage order, all
   inside that single `produce f` (no `consume` between stages). Printing a
   *stage's* loop nest (§5) means working through *that stage's* dimension list
   (after its own §6 transforms; an update stage's list also contains its
   `RVar`s) from its outermost dimension inward, and at each dimension:
     * if that dimension was declared elided (§8), skip its `for` line but still
       treat the level as a valid injection site;
     * if this `(f, stage, dim)` level is the **store level** of some Func `h`
       whose compute level is deeper (§9), open an `h`'s `store h:` node here
       first; everything emitted below at this level — the intervening loops and
       `h`'s own `produce`/`consume` further in — falls inside that `store h:`;
     * inject the Funcs filed at this `(f, stage, dim)` level (from step 3) —
       each as a `produce`/`consume` pair whose `consume` wraps the rest of the
       stage's body;
     * descend to the next-inner dimension, bottoming out at the leaf
       `f(...) = ...`.
   Injection is recursive: an injected Func's own loop nest is emitted the same
   way, so a Func filed inside a Func that is itself filed inside a third nests
   accordingly (§8). A Func scheduled `store_root()` is the special case where
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
