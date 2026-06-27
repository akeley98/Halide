# Halide scheduling and the loop nest

This document explains how Halide turns a *scheduled pipeline* into a *loop
nest*, at the level of detail needed to predict the output of
`Func::print_loop_nest()` by hand. It is built up holistically: each section
adds to a single mental model rather than describing an isolated feature.

> Scope of this revision: the programming model, pure Funcs, update (reduction)
> definitions with `RDom` / `RVar`, the default (inline) schedule,
> `compute_root`, `compute_at`, `store_at` / `store_root`, `hoist_storage` /
> `hoist_storage_root`, the loop transforms `split` / `fuse` / `reorder` /
> `tile`, and the `print_loop_nest()` output format. Wrappers (`in`/`clone_in`),
> `rfactor`, loop-type directives (`parallel`/`vectorize`/`unroll`), and GPU
> scheduling are deferred to later revisions.

---

## 1. The programming model

A Halide program is built in two separable parts:

1. **The algorithm** — *what* each pixel's value is. You declare `Func`s and
   define them as pure mathematical functions of their argument `Var`s, plus
   optional **update definitions** that refine those values (§3).
2. **The schedule** — *when and where* each value is computed and stored. This
   is expressed by scheduling directives (`compute_root`, `compute_at`, …)
   attached to each `Func` (§§5–9).

Crucially, the schedule never changes the *result*; it only changes the order
of computation, the amount of redundant recomputation, and the temporary
storage used. This document is only about how the program maps to a loop nest.

This split drives the document's order: §§2–3 cover the loops implied by the
**algorithm alone** (a single Func, possibly with update stages), and §§5–9
add the **schedule** that places and reshapes those loops across the pipeline.

### Objects and their conceptual state

* **`Var`** — a name for a dimension / loop variable. It carries no state
  beyond its identity (its name). Vars are the formal parameters of a pure
  definition and the handles you later name in scheduling calls.

* **`Func`** — a *handle* to a shared, mutable function definition. Copying a
  `Func` produces another handle to the *same* underlying function; scheduling
  through either handle affects the one function. The conceptual state of a
  Func is:
    * its **name** (used for printing; see §10, and computation-order
      tie-breaking, see §6),
    * its ordered list of **stages**: an initial (pure) definition plus zero or
      more **update definitions** (§3). Each stage has its own ordered list of
      **loop dimensions** (the `Var`s/`RVar`s that drive its loops; the first
      listed is the *innermost* loop — see §3),
    * the set of **other Funcs it reads from** (its *producers*), derived from
      the right-hand sides (and update left-hand-side indices) of all its
      stages,
    * its **compute level** and **store level** — `inline` (the default),
      `root`, or `at(site func, var)` — set by the schedule (§§5–8). These apply to
      the Func as a whole (all stages move together).
    * a per-stage **fuse level**, set by `compute_with` (§14) and *empty* by
      default. A stage with a fuse level is not computed in its own loop nest but
      **interleaved into another stage's**, the two sharing their outer loops;
      the connected set of stages so tied together forms a **fused group**.
      Unlike the compute/store level, this is set *per stage*, not whole-Func.

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

* **`RDom` / `RVar`** — a *reduction domain* and its *reduction variables*. An
  `RDom r(min, extent, …)` declares one or more `RVar`s (`r.x`, `r.y`, …, or
  just `r` for a 1-D domain) used in **update definitions** (§3). An `RVar`
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
store <f>:          # f's storage scope, shown only when it differs from compute (§8)
```

Indentation is containment. `produce f` contains the loops that compute `f` and
ends at the matching `consume`/dedent; everything that reads `f` is nested
under `consume f`. A `for` contains its loop body. The leaf `f(...) = ...` is
the body executed at the innermost point. `consume`/`store` arise only once the
schedule places multiple Funcs or separates storage from computation, so they
are introduced with the schedule (§§5–8); §3 needs only `produce`, `for`, and
the leaf.

Two cosmetic details are *not* part of the model and are normalized away by the
test harness (`../canonicalize.py`): the exact loop-variable names, and constant
loop bounds (`for x in [0, 7]`). What *is* significant: the produce/consume
nesting, the number and nesting of `for` loops, their order, and (in later
revisions) their type (`parallel`, `vectorized`, …).

---

## 3. A single Func's loops and its stages

This section is pure *algorithm*: the loops below follow from a Func's
definitions alone, with no scheduling yet. (For the output Func — or any Func
realized on its own — this is exactly what appears inside its `produce`.)

### The dimension list

Inside `produce f`, a stage of a Func is computed by a loop nest over an ordered
list of **loop dimensions**:

> Each definition (stage) carries an ordered **dimension list**, *innermost
> first*. For the pure definition it starts as the argument `Var`s in order —
> the first argument is the innermost dimension — with an implicit `outermost`
> sentinel pinned at the end (which you can ignore). The stage emits **one `for`
> loop per dimension**, printed *outermost first* (the reverse of the list),
> with the leaf `f(...) = ...` at the center.

So `f(x, y, c) = ...` has dimension list `[x, y, c]` and produces, from outside
in, `for c: for y: for x:`. This is row-major traversal: the first dimension
varies fastest. (A scheduling directive, `reorder` (§9), can change this order;
`split`/`fuse`/`tile` (§9) can change the *number* of loops.)

```
produce f:
  for c:
    for y:
      for x:
        f(...) = ...
```

### Update definitions: a Func may have several stages

A Func may have **update definitions**: extra assignments, written after the
initial one, that *modify* the Func's values in place. The initial (pure)
definition plus the updates form an ordered list of **stages**.

```cpp
Func hist("hist");
hist(x) = 0;              // stage 0: the pure / initial definition
RDom r(0, N, "r");
hist(in(r)) += 1;         // stage 1: an update definition
```

Each stage runs to completion before the next begins, and they all write to the
**same** storage. A Func with `k` update definitions has `k + 1` stages,
numbered `s0` (pure), `s1`, …, `sk` in definition order. Stages are part of the
algorithm — they say *what* `hist` is — so they need no scheduling to
understand.

#### Stages share one `produce`

All of a Func's stages are emitted **inside the single `produce f` block**, as
consecutive sibling loop nests — *not* as separate `produce`/`consume` blocks.
There is no `consume` between stages; once the Func is scheduled, a consumer's
`consume f` (if any) wraps the whole thing, because a reader sees the Func's
*final*, post-update values. For the histogram above (see
[examples/hist_1d.cpp](examples/hist_1d.cpp)):

```
produce hist:
  for x:              # stage 0: initialise hist(x) = 0
    hist(...) = ...
  for r:              # stage 1: the scatter update
    hist(...) = ...
```

#### A stage's loops: free `Var`s plus `RVar`s

Each stage has its **own dimension list**, built from its own left-hand side:

> An update stage loops over the **free `Var`s** appearing on its left-hand
> side **plus the `RVar`s** of any `RDom` it uses. Default order, innermost
> first: the `RVar`s are innermost — and *within* the `RVar`s the first-declared
> dimension (`r.x`) is the **innermost** loop, matching the `Var` convention
> that the first dimension varies fastest. This order follows the `RDom`'s
> **declaration** order, *not* the order the `RVar`s happen to appear in the
> update expression: a stage that writes `r.y` before `r.x` still loops with
> `r.x` innermost ([examples/update_rvar_decl_order.cpp](examples/update_rvar_decl_order.cpp)).
> The free pure `Var`s sit outside the `RVar`s
> (in the usual order, first LHS argument innermost among the pures). A
> pure dimension whose left-hand-side slot is occupied by an `RVar` or a general
> expression (e.g. the `in(r)` index in the histogram, or `f(x, r)`) does
> **not** produce a loop in that stage.

`RVar` loops print like any other (`for r in [min, max]`); the harness drops the
constant bound, so they read as plain `for`. A `k`-dimensional `RDom`
contributes `k` nested reduction loops. So `f(x, y) += in(x + r.x, y + r.y)`
with a 2-D `RDom` gives the update stage loops `for y: for x: for r.y: for r.x:`
(`r.x` innermost; [examples/update_2d_rdom.cpp](examples/update_2d_rdom.cpp)),
while the pure stage `f(x, y) = 0` just gives `for y: for x:`. A reduction with
no free variable on the left, like the histogram scatter, gives a stage with
only the reduction loop(s).

* [examples/sum_reduction.cpp](examples/sum_reduction.cpp): `f(x) = 0; f(x) +=
  in(x, r)` — the pure stage loops over `x`, the update stage over `x` then the
  reduction `r`.
* [examples/two_updates.cpp](examples/two_updates.cpp): three stages (`s0`,
  `s1`, `s2`) printed in order inside one `produce`.

So the number of `for` loops a stage emits equals the length of *that stage's*
dimension list. (Two things in the schedule modify this: `compute_at` can elide
single-point loops, §7; and `split`/`fuse`/`reorder`/`tile` reshape a stage's
dimension list, §9 — and those reshape each stage independently.)

> One legality note that this document does **not** model mechanically: an
> `RVar` loop is *ordered*, so reordering two `RVar`s — or otherwise reshuffling
> them past each other — is rejected unless the reduction is associative *and*
> commutative, which Halide decides by analysing the update's arithmetic.
> Predicting that needs the actual expression semantics, which is out of scope
> here, just like bounds inference for loop elision (§7).

---

## 4. Terminology: pure/non-pure, realized, inline

Three pairs of terms run through the rest of this document. They are worth
pinning down precisely, because one of them ("inline") is overloaded — by Halide
itself.

* **Pure vs. non-pure Func.** A Func is **pure** if it has *only* its initial
  definition — no update definitions (§3). A Func with one or more update
  definitions is **non-pure**. (This is exactly Halide's `Function::is_pure()`.)

* **Realized vs. non-realized.** A Func is **realized** when it is computed into
  an allocated buffer somewhere in the nest — it gets its own `produce` (and,
  where something reads it, `consume`) block. A **non-realized** Func has no
  block of its own: its definition is pasted into every call site (textual
  substitution), so it never appears in the loop nest at all.

* **Inline.** "Inline" is the name of a Func's *default compute level* — the
  other two levels, set by the schedule, are `root` (§6) and `at` (§7). It is
  tempting to read "inline" as a synonym for "non-realized," and for a **pure**
  Func that is exactly right: a pure inline Func is textually substituted and
  does not appear. **But "inline" is a *level*, not a promise of
  non-realization.** A non-pure Func cannot be substituted (a reduction is not
  an expression), so the inline level *realizes* it instead — the awkward case
  deferred to §11. Until then, every inline Func we discuss is pure (hence
  non-realized).

> **Terminology wart — Halide's, not this document's.** Halide applies "inline"
> to the default level of *all* Funcs, including non-pure ones it must realize:
> its own `compute_inline()` documentation says a Func with an update
> definition, left inline, "gets computed as close to the innermost loop as
> possible" (§11). So in Halide **"inline" and "realized" are not opposites** —
> the true opposite of "realized" is "textually substituted." This document
> keeps "inline" for the level and "realized/non-realized" for whether a block
> is emitted, and says "pure inline" when it means the substituted case.

---

## 5. The default schedule: inlining

We now turn to the *schedule*. By default every Func (§4) except the
output is **inlined**. For pure functions, this means non-realized: it has no loops
and no `produce`/`consume` of its own. Wherever a consumer reads it, its definition is
substituted in, as if textually pasted — it simply *disappears* from the loop
nest.

So a pipeline of pure Funcs with no scheduling at all collapses to a single
loop nest over the output's dimensions, with every producer folded into the
output's leaf. See [examples/inline_default.cpp](examples/inline_default.cpp):
`producer` is read twice by `consumer` but never appears; only `consume`'s
loops are emitted.

Inlining trades memory for redundant computation: each use re-evaluates the
producer (here `producer` is effectively computed twice per output pixel). The
output Func is never inlined — it is always realized at the root (§6).

Two Funcs are *not* covered by this default and are picked up later:

* the **output**, always realized at root (§6);
* a **non-pure** Func (one with update definitions), which is *also* left at the
  "inline" level by default but, being unable to be substituted, is realized at
  the innermost point of each use. Because that behavior leans on `compute_at`
  (§7) to describe, it is deferred to §11; it is rare in practice and probably
  an inefficient choice, so postponing it keeps the cleaner concepts uncluttered.

---

## 6. `compute_root`: realize once at the top

`f.compute_root()` sets `f`'s compute level to `root`: `f` is computed in full,
once, at the outermost level, *before* anything that uses it. It gets its own
loop nest (§3, all of its stages) wrapped in `produce f`, and the rest of the
program is nested under `consume f`. Note that `consume f` is **not** selective:
it mechanically wraps everything emitted after `produce f` — typically the
entire remainder of the pipeline — regardless of which parts actually read `f`.
(The name reflects that `f`'s values are now available to be consumed there, not
that the wrapped code is exactly `f`'s readers.)

This is the first source of `consume` nesting worth stating plainly: when
several Funcs are realized in sequence, their `consume` blocks **nest** — the
rest of the program, *including any later producers*, sits inside the current
`consume` rather than appearing as a flat list of siblings.

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

Realization order is a topological sort of the *whole* graph — **every** Func in
the pipeline gets a slot, including the ones that end up inlined (Halide computes
this order over the full environment; inlining is decided separately, later).
What makes inlined Funcs vanish from the *printed* nest is not their absence from
this order but the fact that they are never **realized**: a pure inline Func is
substituted into its callers rather than given a `produce` block (§5). Keeping
them in the order is exactly what lets them **transmit dependencies**: if inlined
`b` reads rooted `a`, then `b` sits between `a` and any consumer of `b` in the
order, so `a` precedes that consumer. In `box_blur`, `output` inlines
`blur_y`→`blur_x`→`input_16` (rooted), so `input_16` is realized before
`output`.

#### Tie-break: which sibling producer goes first

A topological sort leaves freedom whenever a consumer reads two producers that
do not depend on each other — e.g. `output(...) = g(...) + h(...)` with both `g`
and `h` at root. Halide breaks the tie **deterministically by name, not by the
order they appear in the defining expression**:

> Sibling producers are ordered by **name prefix** (alphabetically), then by
> **first-visitation order**, then by full name. The "prefix" is the name with any
> `$n` uniqueness suffix and any trailing digits removed.

The "walk the pipeline" that defines first-visitation order is a pre-order
depth-first traversal from the output(s): record each Func the first time it is
reached, then descend into the Funcs it calls — in the order those calls first
appear in its definition — skipping any Func already recorded. A Func's
visitation index is its position in that record. (This is only a tie-break for
equal prefixes; the topological producer-before-consumer constraint and the
prefix comparison dominate it.)

So in [examples/tiebreak_realization_order.cpp](examples/tiebreak_realization_order.cpp),
even though the expression is written `b1d(x) + a2d(x, y)`, `a2d` is realized
first because `"a2d" < "b1d"`. The left-to-right order of `+` is irrelevant. The
first-visitation tie-break only matters when two prefixes are equal — e.g.
auto-named or numbered Funcs sharing a prefix; in
[examples/tiebreak_visitation_order.cpp](examples/tiebreak_visitation_order.cpp)
two producers share the prefix `b`, and the one *visited* first is realized
first even though it is alphabetically later. This same ordering decides the
order of sibling producers filed at any single `compute_at` level, not just root
(§7).

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

The named `var` must be a dimension of `g` — one of `g`'s argument `Var`s, or a
dimension a loop transform created on `g` (§9), or one of `g`'s `RVar` loops
(see "reduction loops as sites" below).

### Multiple producers of one consumer

When a consumer reads several producers, each producer is placed according to
*its own* compute level; they do not share a single flat `consume` block.

* **Both at the same level** (e.g. both `compute_at(output, y)`, or both at
  root): they form a nested produce/consume chain at that level, ordered by the
  realization-order tie-break (§6) — alphabetical by name, *not* expression
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
  (§6) and never nests inside the consumer; the `compute_at` producer nests
  inside the consumer's loops as usual. See
  [examples/producers_root_and_at.cpp](examples/producers_root_and_at.cpp).

### Loop elision: a `compute_at` Func may emit fewer loops than its dimensions

A root Func always emits one loop per dimension (§3). A `compute_at` Func does
**not**, in general. Halide computes only the *region* of the producer needed
per iteration of the site func, and a dimension whose needed extent is a single
point becomes an extent-1 loop that Halide **simplifies away entirely** — no
`for` line is printed for it. Conceptually:

> A dimension `d` of `f.compute_at(g, L)` survives as a loop iff the values of
> `f`'s `d`-coordinate read by `g` span more than one point as `g`'s loops
> *inner to `L`* run. Reading `f` at a multi-tap stencil in `d`, or at an index
> that varies with an inner site-func loop, keeps the loop; a single-point read
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

`micro_halide_collapses(f, {vars...})` is a no-op under real Halide; it tells
micro_halide which loops to drop. (It is declared per stage, mirroring the
schedule API: `micro_halide_collapses(f, …)` targets the pure stage and
`micro_halide_collapses(f.update(N), …)` an update stage.) The split between
*structure* (taught here, derived from the schedule) and *elision* (declared) is
described in the README. The loop *structure* — produce/consume placement,
ordering, and the surviving loops — is fully determined by the schedule as
described in §§5–9.

### An elided loop is still a `compute_at` injection site

Eliding a loop only removes its `for` line; the loop's *position* in the nest is
preserved. So a Func computed at an elided loop is still injected there, as a
prefix of the site func's body, outside any surviving inner loops of the site func. See
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

### What `(g, var)` points to when `g` has several stages

`compute_at` names a **site Func and a `Var`**, never a stage — yet a Func with
update definitions (§3) has several stages, each a separate loop nest that may
*each* contain a loop named `var`. So which one does `f.compute_at(g, var)`
target? The answer: **all of them, but `f` is only materialized where it is
actually read.** Concretely:

> `(g, var)` denotes the loop named `var` in **every** stage of `g` (the stage
> is left unspecified). When the nest is built, `f`'s realization is injected
> just inside that loop in each stage of `g` **whose body uses `f`** — where
> "uses" counts *transitively*: directly, or through another producer already
> realized in that body (see "indirect consumer" below). A stage that never uses
> `f` gets nothing. So `f` lands once per *using* stage, each as its own
> `produce f`/`consume f` (the stages are sibling nests, §3).

Two consequences:

* A producer read inside a reduction can be `compute_at` that **`RVar` loop**;
  it is placed inside that one stage's reduction loop, because only that stage
  reads it ([examples/producer_at_rvar.cpp](examples/producer_at_rvar.cpp)):

  ```
  produce f:
    for x:                      # stage 0 -- does not read p, gets nothing
      f(...) = ...
    for x:                      # stage 1 -- reads p
      for r:
        produce p:              # p injected inside the reduction loop
          p(...) = ...
        consume p:
          f(...) = ...
  ```

* When several stages read `f` and all share the named loop, `f` is injected
  into **each** of those stages
  ([examples/cross_stage_compute_at_shared.cpp](examples/cross_stage_compute_at_shared.cpp)).
  Each injection is independent, so `f`'s *required region* — and hence how many
  of its loops survive (§7 elision) — is computed **per stage**: the same
  `compute_at` can leave `f` with no loops in one stage and a real loop in
  another, if the two stages read different ranges of `f`. (This per-stage
  elision is why `micro_halide_collapses` is declared per stage; see above.)

Going the other way, `f`'s own compute level applies to `f` as a **whole**: the
entire `produce f` — all of `f`'s stages — is realized together at the chosen
site. Computing a Func with updates inside a consumer drops its whole
multi-stage block at that loop level
([examples/func_update_compute_at.cpp](examples/func_update_compute_at.cpp)).

### Computing at an *indirect* consumer's loop

`f.compute_at(h, v)` does **not** require `h` to read `f` directly. It is enough
that `f`'s *use* lands inside `h`'s `v` loop — which is what happens when some
intermediate Func `g` reads `f` and `g` is itself computed at (or within) `h`'s
`v` loop. The injection rule above is "inject `f` wherever the loop body at the
chosen level uses `f`", and that test is applied to the body *after* inner
producers have been placed: once `g`'s realization sits at `h.v`, the body there
calls `f` through `g`, so `f` is injected at `h.v` as a prefix — *before* `g`:

```
produce h:
  for y:
    produce f:          # f at h.y, before g, because the body here uses f (via g)
      ...
    consume f:
      produce g:        # g reads f and is itself computed at h.y
        ...
      consume g:
        ... h reads g ...
```

See [examples/transitive_compute_at_outer.cpp](examples/transitive_compute_at_outer.cpp)
(a non-pure `g` computed at `h`'s `y`, with `f.compute_at(h, y)` legal even
though only `g` reads `f`). This is also why `f`'s legal sites (next subsection)
run **through** `g`: `f`'s use is enclosed by `h.y` and then `g`'s own loops, so
those are the candidates — but **not** `h`'s loops that live in `consume g`
(e.g. `h`'s inner `x`), which execute *after* `g` and so do not enclose `f`'s use
([examples/neg_transitive_compute_at_inner.cpp](examples/neg_transitive_compute_at_inner.cpp)).

This pull-in is decided **per stage**, and only where the intermediate is itself
present. The level `(h, v)` (stage left unspecified, above) names a `v` loop in
*every* stage of `h`, but the rule "the body at this level uses `f`" is applied
to **each stage's own body**, built by the same use-gating: an intermediate `g`
is realized in a stage's body only if *that stage* actually uses `g` (directly,
or transitively through a further intermediate present in that stage). So `g`
can pull `f` into a stage **only where `g` itself lands** — a stage that uses
neither `g` nor `f` gets no `produce f`, even though it has a `v` loop in the
family. Concretely, when `f.compute_at(h, v)` is reached only through `g`, the
two conditions stack: `f` appears in stage `s` iff `s`'s body uses `g` at the
`v` loop *and* `g` uses `f`. This matters most after `rfactor`, whose
intermediate has a pure stage (reading nothing) beside its reducing stage:
filing the indirect producer at the intermediate injects it only into the
reducing stage. [examples/rfactor_indirect_at_intm.cpp](examples/rfactor_indirect_at_intm.cpp)
(`h.compute_at(intm, u)` through `g`, injected into the intermediate's reducing
stage but **not** its pure stage), [examples/rfactor_indirect_nested.cpp](examples/rfactor_indirect_nested.cpp)
(`g` at the intermediate, `h` nested in `g`), and
[examples/neg_rfactor_indirect_h_at_intm.cpp](examples/neg_rfactor_indirect_h_at_intm.cpp)
(illegal: with `g` at root, the intermediate's `u` loop no longer encloses `h`'s
use) exercise the three cases. The pull-in fires in *every* using stage, not just
one: in [examples/transitive_multistage_inject.cpp](examples/transitive_multistage_inject.cpp)
two update stages both read `g`, so `h.compute_at(f, x)` (through `g`) is injected
into both — but still not the pure stage.

### When a `compute_at` is illegal

`f.compute_at(g, v)` is not always legal. First, what a **level** is, because it
is the crux: `(g, v)` is *not* a pointer to one loop. With the stage left
unspecified (previous subsection) it denotes `g`'s `v` loop in **every** stage of
`g` — a whole **family** of loop locations, `g.s0.v`, `g.s1.v`, …, one per stage
that has `v` as a loop variable (§3, §9).
(Halide calls such a `(Func, Var)` pair a *loop level*; the candidate levels at
which `f` may be computed are its legal *sites* — "site" and "level" are the same
kind of object, a `(Func, Var)` pattern, plus the special `root` and `inline`.)
The Func of a level — the one whose loop `f` is placed inside, `g` here — is the
**site func** (Halide's codegen has no dedicated name for it; it is just
`loop_level.func()`). Throughout, "site func" means this Func; bare "site" /
"level" means the `(Func, Var)` location.
`compute_at` gives `f` this single level, and `f` is realized at it in each stage
of `g` that reads `f` — so the several `produce f` blocks share the *same level*
`(g, v)` but sit at *different concrete loops* in the family.

Say the level **encloses** a read of `f` when that read lies inside *some* member
of the family (some `g.s?.v`). The schedule is legal iff the level encloses
**every** read of `f` — i.e. the family of `g.*.v` loops *collectively* covers
them all. So "`v` must enclose every read" never means one loop containing
everything; it means every read sits inside *some* loop of the family. A read in
a **different** consumer Func, or at `g`'s own outer scope, lies inside no
`g.*.v` loop at all, so the level cannot cover it — the usual way to be illegal.

Halide computes this directly: it walks the *whole* loop nest and, at **every
place `f` is read** (including indirectly through other functions `g` consumes),
Halide intersects the stack of `(Func, Var)` levels enclosing that
read; the legal sites are what survive (plus `root`). It is one global
intersection over all reads — you pick a single level, not a different one per
stage. If `(g, v)` does not survive, Halide rejects the schedule with *"Func f is
computed at the following invalid location"* (and lists the legal ones); no loop
nest is produced.

(Choosing a *different* level per read is exactly the freedom the **default
inline** schedule has and a single `compute_at` does not — which is why the
inline default of a non-pure Func cannot, in general, be rewritten as one
`compute_at`; §11.)

The ways `(g, v)` falls outside the surviving set:

* **`v` is missing from a stage that reads `f`.** `v` must name a loop that, *in
  each stage of `g` that reads `f`*, encloses that stage's use — only then does
  every reading stage have somewhere to inject `f`. Two flavors:
    * `v` is not a dimension of `g` at all, so no stage has a loop to inject into
      ([examples/neg_compute_at_bad_var.cpp](examples/neg_compute_at_bad_var.cpp)).
    * `v` exists in some stages but not in a *reading* one. A reduction `RVar`
      lives only in its own stage, so computing `f` at it is legal when that is
      the **only** stage reading `f`
      ([examples/producer_at_rvar.cpp](examples/producer_at_rvar.cpp)) but illegal
      when another stage *also* reads `f` and has no such loop
      ([examples/neg_compute_at_update_rvar.cpp](examples/neg_compute_at_update_rvar.cpp):
      `p` is read by both the pure and the update stage, so the update's `r` loop
      is not a legal site). When the loop *is* shared by every reading stage
      (e.g. a pure `Var` carried through all of them) the site is legal and `f`
      is injected into each
      ([examples/cross_stage_compute_at_shared.cpp](examples/cross_stage_compute_at_shared.cpp)).

* **`g` is not a consumer.** No stage of `g` reads `f` (directly or through Funcs
  inlined into it), so nothing in `g` needs `f` —
  [examples/neg_compute_at_nonconsumer.cpp](examples/neg_compute_at_nonconsumer.cpp).

* **`f` is read outside `g`.** Every realization of `f` sits inside `g`, so a use
  of `f` in a *different* Func — or at `g`'s own outer scope — is never reached
  and would read undefined values. The chosen level must enclose those uses too;
  when `f` is read at two unrelated places the only level enclosing both is
  `root`
  ([examples/neg_compute_at_two_consumers.cpp](examples/neg_compute_at_two_consumers.cpp)).

The last case is the fundamental one: `f` placed inside one site func can only
feed reads within that site func. Feeding consumers that live at different, non-nested
locations is exactly what the wrapper Funcs `in()` / `clone_in()` (a later
milestone) enable; until then such a schedule is simply illegal.

This single principle — *the level must enclose every read of `f`* — is the
whole rule, and it does **not** grow with new features: later directives do not
add compute_at-legality cases, they only **reshape the loop nest** the principle
is evaluated against. `compute_with` (§14) is the example to keep in mind: fusing
`g` into `f` moves `g`'s reads into `f`'s body, so a site that used to enclose
every read of a producer can stop doing so (and a producer computed at a fused
*child* is illegal because the child owns no such loop). Both are this same
"enclose every read" check, re-evaluated on the post-fusion structure — not new
rules.

The illegal cases above are *negative* examples — both Halide and `micro_halide`
must reject them (exit with an error) rather than print a loop nest — while the
legal ones cited (`producer_at_rvar`, `cross_stage_compute_at_shared`) print a
nest the two must match.

---

## 8. `store_at` / `store_root`: storage level vs. compute level

So far a Func has had a single *compute level* (§6, §7) that fixes both where it
is computed and where its storage is allocated. These can be separated. Besides
its compute level, a Func has a **store level**: the loop at which its buffer is
allocated. By default the store level **equals** the compute level. Two
directives change it (and *only* it — they do not move the computation):

* `f.store_at(g, v)` — allocate `f`'s storage in site func `g`'s loop over `v`.
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
(and the site-func loops between the store level and the compute level fall inside
it). The `store f:` node wraps `f`'s *whole* realization — all of its stages
(§3), since the store level is per-Func.

The store node follows `f` **per site-func stage**, just as the `produce`/`consume`
does (§7). When the **site func** of the store/compute level has several stages, the
level `(site func, v)` names a `v` loop in every one of them, but `f` is computed only
in the site-func stages whose body uses it — so the `store f:` node appears at `v` in
exactly those stages, never in a site-func stage that merely has a `v` loop but never
computes `f`. A producer read only in a consumer's *update* stage therefore gets
its `store` node in that stage alone, not in the pure stage
([examples/store_at_update_stage.cpp](examples/store_at_update_stage.cpp); and
[examples/rfactor_intm_store_at.cpp](examples/rfactor_intm_store_at.cpp), an
`rfactor` intermediate stored at the merge stage's outer loop but absent from
`f`'s pure stage).

For `g.store_at(f, y).compute_at(f, x)` — store at the outer loop `y`, compute at
the inner loop `x` (see [examples/store_at_compute_at.cpp](examples/store_at_compute_at.cpp)):

```
produce f:
  for y:
    store g:          # at the store level (f's y)
      for x:          # site-func loop between store and compute level
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

When the site func of the compute level is itself an intermediate Func, the `store`
node still lands at the named store loop and wraps that Func's whole realization
(its `produce` *and* `consume`); see
[examples/store_root_chain.cpp](examples/store_root_chain.cpp). A `store_root`
Func with update stages wraps all of them
([examples/store_root_update.cpp](examples/store_root_update.cpp)).

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

## 9. Reshaping a Func's loops: `split`, `fuse`, `reorder`, `tile`

These four directives rewrite a **stage's dimension list** (§3) — they add,
remove, rename, and reorder its loops. They change *only that stage's own loops*
(and the dimension names you may use as `compute_at`/`store_at` sites); they
never move the Func relative to other Funcs and never change which values are
computed. They apply **per stage**: `f.<directive>(…)` schedules the pure stage,
and `f.update(i).<directive>(…)` schedules update stage `s(i+1)`, leaving the
others untouched ([examples/update_stage_split.cpp](examples/update_stage_split.cpp),
[examples/update_stage_reorder.cpp](examples/update_stage_reorder.cpp)). Within a
stage the transforms treat `RVar`s exactly like `Var`s in the list.

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
site-func loops fall inside that producer's block.
[examples/reorder_topological.cpp](examples/reorder_topological.cpp) reorders a
consumer's dimensions so the producer's `compute_at` site moves to the innermost
loop; contrast [examples/reorder_baseline.cpp](examples/reorder_baseline.cpp),
the same pipeline without the `reorder`. (`reorder` also becomes directly
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
filed at site-func dimension `d` is injected just inside `d`'s loop, with the
site-func loops *inner* to `d` (those earlier in the list) falling inside the producer's
`consume` — the same rule as §7, now applied to the *post-transform* list. In
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
  is just the §7 "site must be a current loop" rule applied after a transform.
* `reorder` must reference each dimension at most once.

---

## 10. Function names and identity in the output

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

## 11. Inlined non-pure Funcs (the deferred default)

§§5–10 assumed every inline Func is pure, and therefore non-realized (§4). This
section handles the one leftover case: a **non-pure** Func (one with update
definitions, §3) left at the default **inline** level. It is uncommon — it is
probably not the fastest choice — so it is isolated here rather than woven
through the earlier sections.

A non-pure Func cannot be textually substituted (a reduction is not an
expression), so the inline level **realizes** it — but at the latest, innermost
place possible: Halide materializes it *at each use, just inside the innermost
loop enclosing that use*, recomputing it from scratch every iteration (Halide's
`compute_inline()` doc: a Func with an update definition, left inline, "gets
computed as close to the innermost loop as possible"). For `g(x, y) = f(x)` with
`f` an unscheduled reduction (see
[examples/update_default_inline.cpp](examples/update_default_inline.cpp); and
[examples/weird_histogram_sampling.cpp](examples/weird_histogram_sampling.cpp),
a histogram feeding a pointwise consumer):

```
produce g:
  for y:
    for x:
      produce f:          # all of f's stages, recomputed every (x, y)
        f(...) = ...
        for r:
          f(...) = ...
      consume f:
        g(...) = ...
```

### When it equals a `compute_at`, and when it does not

If `f` is read at a **single** loop depth, this default is *exactly*
`f.compute_at(consumer, v)` with `v` the innermost loop enclosing the use — the
nests are byte-identical. Both examples above are this common case, which is why
the rest of the document can treat the non-pure default as "a default
`compute_at` at the innermost use" and lose nothing.

It stops being expressible as **any** single `compute_at` once `f` is read at
*different depths in different stages* of a consumer, because the inline level
places `f` at each use's *own* innermost enclosing loop, **independently per
use**, whereas `compute_at(consumer, v)` can name only one loop. Take non-pure
`f` read as `f(x)` in a consumer's pure stage (depth `x`) and as `f(r)` inside
its update stage's reduction (depth `r`):

* the inline default puts `f` inside `x` in stage 0 **and** inside `r` in stage 1;
* `compute_at(consumer, x)` is too shallow for stage 1 (it puts `f` at `x` there,
  not inside `r`);
* `compute_at(consumer, r)` is **illegal** — `r` exists only in the update stage,
  so it does not enclose the pure stage's use of `f` (§7's legal-site rule).

So inline-of-non-pure is genuinely *more* than "a default `compute_at`": it is a
per-use-site materialization, equal to a `compute_at` only when every use shares
a depth. This is the deepest reason "inline" is not just a compute level with one
site (§4's wart): for a non-pure Func it means "recompute at the innermost point
of *each* use." (A model of the loop nest must therefore place it per use site,
not by rewriting `f` to a single `compute_at`.)

---

## 12. `rfactor`: factoring an associative reduction into a new Func

`rfactor` is a scheduling directive called on an **update stage**
(`f.update(i).rfactor(...)`), but it is unusual: most directives only reshape
existing loops, whereas `rfactor` changes the *structure* of the algorithm. It
**creates a brand-new intermediate Func** and **rewrites** the update stage it
was called on. It exists to parallelise or vectorise a reduction: a reduction
loop carries a dependence across its iterations (each adds onto the running
result), so it cannot be parallelised directly; `rfactor` splits the work into
independent partial reductions — which *can* be parallelised — plus a final
merge.

`rfactor` takes a list of `{RVar, Var}` pairs, the **preserved** vars (the
shorthand `rfactor(r.x, u)` is one pair). Each named `RVar` of the stage is
mapped to a fresh **pure** `Var`. Conceptually, given

```cpp
f(x)  = 0;
f(x) += in(r.x, r.y);             // update stage: reduces over r.x and r.y
Func intm = f.update(0).rfactor(r.y, u);   // preserve r.y as a new pure Var u
```

the state becomes two Funcs ([examples/rfactor_basic.cpp](examples/rfactor_basic.cpp)):

```cpp
// the new intermediate Func (auto-named "f_intm"):
intm(x, u)  = 0;                  // a pure stage
intm(x, u) += in(r.x, u);         // an update stage; r.y has become the pure Var u
// f's chosen update stage, rewritten to MERGE the partials:
f(x)  = 0;                        // (pure stage unchanged)
f(x) += intm(x, r.y);             // now reduces over r.y only, reading intm
```

### What `rfactor` builds

Splitting the rule into the two Funcs it produces:

* **The intermediate Func** (named `<orig>_intm`; the harness normalises names
  away, so what matters is that it is *one new distinct Func*). It is a normal
  multi-stage Func:
    * Its **pure stage**'s dimension list is the original Func's pure-stage
      dimensions, followed by the new pure `Var`s in `preserved` order — the new
      vars **outermost**. (Innermost→outermost for `rfactor(r.y, u)`: `[x, u]`.)
    * Its **update stage** is a *copy of the original update stage's dimension
      list* — including any `split`/`reorder`/`tile` already applied to it
      (§9) — with each **preserved** `RVar` replaced in place by its new pure
      `Var`. The **non-preserved** `RVar`s stay as reduction loops (they are the
      reduction the intermediate still performs); the loop order is otherwise
      unchanged. It reads whatever the original update read.
* **The original Func's chosen update stage is rewritten** into the *merge*: its
  dimension list keeps the free `Var`s and the **preserved** `RVar`s (still
  `RVar`s here), and **drops** the non-preserved `RVar`s. Its body now reads the
  intermediate, so **the intermediate becomes a producer of the original Func**
  (it gets a slot in the realization order before `f`, §6).

The preserved `RVar`s thus end up reduced in the *merge* (still `RVar`s in the
original Func) and pure in the *intermediate* (the new `Var`s); the
non-preserved `RVar`s are lifted entirely into the intermediate's reduction.

### Scheduling the intermediate

The intermediate is an ordinary Func returned to you, with its **own default
schedule**. It is non-pure (it has an update), so absent any directive it takes
the **non-pure inline default** (§11): it is realized at its use inside the
merge stage and recomputed for each value of the merge's loops — see
[examples/rfactor_default_inline.cpp](examples/rfactor_default_inline.cpp). That
defeats the purpose, so you normally schedule it: `intm.compute_root()`
([rfactor_basic.cpp](examples/rfactor_basic.cpp)) realizes it once before `f`,
and because it is a plain producer of `f` it can also be `compute_at` any loop
of `f` that encloses the merge's use of it
([examples/rfactor_compute_at.cpp](examples/rfactor_compute_at.cpp)). Its two
stages are scheduled independently, exactly like any Func: `intm` schedules the
pure stage, `intm.update(0)` the partial-reduction stage. So you can
`reorder`/`split`/parallelise the partial reduction on its own
([examples/rfactor_multivar.cpp](examples/rfactor_multivar.cpp), which preserves
two reduction vars of a 3-D `RDom` and reorders the intermediate's update loops).

### Legality and limits

* `rfactor` may only be called on an **update** stage, never the pure stage —
  the pure stage has no reduction to factor.
* The reduction must be **associative** (and **commutative** too, when an inner
  `RVar` is factored out while an outer one is preserved), or `rfactor` errors.
  Like the `RVar`-reorder rule (§3), this is a property of the update's
  *arithmetic*, which this document does not model mechanically (out of scope,
  as with bounds inference).
* Once the intermediate exists, all the ordinary rules apply to it unchanged:
  its compute/store levels (§6–§8), the legality of a `compute_at` on it (§7),
  and the per-stage transforms on its stages (§9).
* A reduction var may be `split` (§9) *before* being factored — the tiled
  histogram of Halide tutorial lesson 18 does
  `split(r.x, rxo, rxi, …).rfactor({{rxo, u}})`, preserving the outer tile index
  and lifting the inner one. This relies on splitting an `RVar` (whose halves are
  themselves reduction loops), an interaction this document does not yet model;
  it is deferred (see progress.txt). The multi-var example above factors several
  whole `RVar`s of one `RDom` instead, which needs no `RVar` split.

---

## 13. `in` and `clone_in`: wrapper and clone Funcs

Both directives create a **new, separate Func** that a chosen set of consumers
read *instead of* the original. They differ in what that new Func computes. In
both cases the new Func is an ordinary Func with the usual **default inline**
schedule (§4–§5), and you schedule it like any other. Left at the default it is
*non-realized*: a pure wrapper or a pure clone is substituted away exactly like
any other inline pure Func (§5), so it has **no visible effect on the nest until
you give it a compute level** (`compute_root`/`compute_at`); a *non-pure* clone
left inline instead follows the non-pure inline default (§11). The example nests
below therefore assume the new Func has been scheduled (e.g. `compute_root`).

### `f.in(g)` — an identity *wrapper*

`f.in(g)` returns a new Func (printed `f_in_g`) whose definition is the pointwise
identity `f_in_g(args) = f(args)`, and it makes `g` read `f_in_g` where it used
to read `f`. The wrapper reads `f`; `f`'s *other* consumers are untouched. The
wrapper is pure, so left at its default it inlines straight back (the nest is
just `f` → `g`, as if the `in` were not there). Once you give it a compute level
(here `f_in_g.compute_root()`) it becomes a distinct node between `f` and `g`
(realization order `f` → `f_in_g` → `g`):

```
produce f:
  ...
consume f:
  produce f_in_g:        # the wrapper reads f
    for ...: f_in_g(...) = ...
  consume f_in_g:
    produce g:           # g now reads f_in_g, not f
      ...
```

The wrapper is schedulable like any Func (`f_in_g.compute_root()`,
`f_in_g.compute_at(g, …)`, …). Two common uses: give a shared producer a
per-consumer staging point (each consumer reads `f` through its own wrapper at
its own level), and repair the "two consumers force `f` to `root`" situation
(§7, [neg_compute_at_two_consumers.cpp](examples/neg_compute_at_two_consumers.cpp)):
wrap `f` separately per consumer so each wrapper has a single consumer and can be
computed inside it. Variants: `f.in(g)`, `f.in({g1, g2, …})` (one shared wrapper
for several named consumers), and `f.in()` (a single **global** wrapper used by
every consumer that has no custom wrapper of its own, either defined before or
after the `f.in()`).

A global wrapper coexists with custom ones. When `f` has both a custom wrapper
(for some consumers) and a global wrapper, a consumer with its own custom wrapper
uses that (**custom takes precedence**), and every other consumer uses the
global wrapper — **except `f`'s own wrappers, which keep reading `f`**. A wrapper
is created precisely to read `f`, so the global redirect never applies to `f`'s
custom or global wrappers themselves: each of them reads `f`, and they sit as
siblings both consuming `f` (see
[examples/in_custom_and_global.cpp](examples/in_custom_and_global.cpp): `g1`'s
custom wrapper and the global wrapper both read `f`, neither reads the other).

### `f.clone_in(g)` — an independent *clone*

`f.clone_in(g)` returns a new Func that is a **copy of `f`'s entire definition**
(all stages and schedule), and makes `g` read the clone. Unlike a wrapper, the
clone *recomputes* `f`'s work rather than reading `f`'s result, so `f` and the
clone are independent and may be scheduled differently.

A clone duplicates `f` itself but **not `f`'s inputs**: the clone reads the
*same* producer Funcs that `f` reads — callees are **shared, not copied**. This
has a sharp scheduling consequence. If `f` reads a producer `p`, then after
`f.clone_in(g)` the Func `p` is read in two places — inside `f` and inside the
clone — so the only level enclosing *both* uses is `root`. A schedule that
computes `p` *inside* `f` (e.g. `p.compute_at(f, x)`) becomes **illegal**: Halide
reports `p` "is used in" both `f` and `f_clone_in_g` and lists
`p.compute_root()` as the only legal location. To give a clone genuinely private
inputs you must clone those inputs too. (The `Func::clone_in` doc's phrase about
"intermediate Funcs along the path" refers to the transitive *caller* chain
between the consumers and `f`, **not** to `f`'s callees; the callees are shared.
See [src_doc: in/clone_in](src_doc/in_clone_in.md) for the verification.)

### Identity in the output, and which consumers are redirected

Each wrapper or clone is a **distinct** Func with its own auto-generated name
(`f_in_g`, `f_clone_in_g`, plus an internal uniqueness suffix the printer
strips); the harness treats it as a separate node (§10 — names are normalized to
positional ids, so what matters is that it is one more distinct Func). They are
*not* "the same Func appearing twice." Only the **named** consumers are
redirected — and, transitively, the *direct callers* on each path down to `f`
(so `f.in(h)` where `h` reaches `f` only through `g` actually redirects `g`); a
global `f.in()` redirects every other consumer.

Redirecting a caller means that caller reads the wrapper/clone in place of `f`,
so `f` keeps only the consumers that were *not* redirected. The two directives
then differ in whether `f` survives:

* an **`in` wrapper always keeps `f` in the pipeline** — the wrapper reads `f`,
  so `f` still has a consumer (and is then realized or inlined per `f`'s own
  schedule, as always);
* a **clone reads `f`'s *inputs*, not `f`** (callees are shared, above), so if
  *every* consumer of `f` ends up redirected to the clone, `f` has no remaining
  reader, becomes unreachable from the output (§1), and **drops out of the nest
  entirely** — the clone takes its place.

So for the chain `h` → `g` → `f` with no other reader of `f`: `f.in(h)` (which
redirects the direct caller `g`) prints `f` → `f_in_h` → `g` → `h` — `f` stays,
now read by the wrapper — whereas `f.clone_in(h)` prints `f_clone_in_h` → `g` →
`h` with `f` **absent**, because `g` now reads the clone and the clone reads
`f`'s own inputs rather than `f`.

### Examples

* [in_basic.cpp](examples/in_basic.cpp) — `f.in(g)` scheduled `compute_root`
  (`f` → `f_in_g` → `g`); [in_unscheduled.cpp](examples/in_unscheduled.cpp) — the
  same wrapper left at its default inlines away (nest is just `f` → `g`).
* [in_compute_at.cpp](examples/in_compute_at.cpp) — the wrapper computed inside
  its consumer (`f_in_g.compute_at(g, y)`).
* [in_multi.cpp](examples/in_multi.cpp) — `f.in({g1, g2})`, one shared wrapper
  for two named consumers; [in_global.cpp](examples/in_global.cpp) — `f.in()`,
  one global wrapper redirecting every consumer.
* [in_two_consumers_fix.cpp](examples/in_two_consumers_fix.cpp) — the positive
  fix for [neg_compute_at_two_consumers.cpp](examples/neg_compute_at_two_consumers.cpp):
  a per-consumer wrapper can be computed inside its single consumer.
* [clone_basic.cpp](examples/clone_basic.cpp) — `f.clone_in(g)` with `f` kept by
  another consumer; the clone and `f` share the callee `p` (one `produce p`).
  [neg_clone_shared_callee.cpp](examples/neg_clone_shared_callee.cpp) — the
  shared-callee gotcha: `p.compute_at(f, x)` is illegal once the clone also reads
  `p`.
* [in_transitive.cpp](examples/in_transitive.cpp) vs
  [clone_transitive.cpp](examples/clone_transitive.cpp) — `h` → `g` → `f`: the
  `in` wrapper keeps `f`; the clone makes `f` drop out.
* [tiebreak_visitation_order.cpp](examples/tiebreak_visitation_order.cpp) — two
  same-prefix producers of one consumer, where the realization-order tie-break's
  *visitation-order* secondary key (§6) decides — the case that arises once
  several wrappers/clones share a name prefix.

### Implementation note

Although the documentation, for simplicity, describes `f.in(g)` or
`f.clone_in(g)` as modifying the consumer `g` to use the
wrapped/cloned `f`, the actual Halide implementation does not mutate
the consumer Funcs at the time you call `in`/`clone_in`: the wrapper
is recorded on `f`, and the consumers' reads are rewritten as a
derived step when the nest is built. Note in particular this
greatly simplifies the interaction between the fallback `f.in()`
wrapper, and other `f` wrappers.

[src_doc: in/clone_in](src_doc/in_clone_in.md) documents the
identity model and that call-rewrite mechanism in detail.


---

## 14. `compute_with`: fusing stages into a shared loop nest

Every directive so far gives one Func its own loop nest. `compute_with` is
different: it takes two stages that would otherwise run in separate nests and
interleaves them into one shared nest. It creates no Func and changes no value —
it only reshapes loops.

### The directive, and the state it records

`b.compute_with(a, v)` is called on the stage being fused in — `b`, the
**child** — and takes the stage to fuse into as its argument — `a`, the
**parent** — at loop level `v`. The argument may be a Func (its initial stage) or
a `Stage` (`a.update(j)`). `compute_with` is per stage:

```cpp
g.compute_with(f, y);                       // fuse g's init stage into f's, at level y
g.update().compute_with(f.update(), y);     // ... and the update stages
```

Like every directive, `compute_with` only records state — a per-stage fuse level
(§1) on the child — and the nest is built from it later (§15). Because the state
is one fuse level per stage, calling `compute_with` again on the same stage
overwrites the previous one (Halide warns). So `f.compute_with(a, y)` then
`f.compute_with(b, y)` fuses `f` with `b` only — it does not create a group
`{f, a, b}`.

### Fused groups are a per-stage relation

Each `compute_with` records a **fuse edge** from a child stage to a parent stage.
The Funcs connected (directly or transitively) by these edges form one fused
group, realized as a unit. Because edges join stages, one Func can sit on several
edges:

* a child can itself be the parent of a further `compute_with` — a chain, still
  one group ([examples/compute_with_chain.cpp](examples/compute_with_chain.cpp));
* a Func's different stages can fuse into different parents — e.g.
  `f.compute_with(g)` with `f.update().compute_with(h)` puts `f, g, h` in one
  group while `f` has two parents
  ([examples/compute_with_two_parents.cpp](examples/compute_with_two_parents.cpp)).

So "parent" is a property of each edge (the argument stage of that
`compute_with`), not of the group: in general there is **no single group
parent**. (When one member is the ancestor of all the rest — the common case:
several children fused directly into one stage, or a chain — that member is
loosely "the group parent", and statements below phrased as "the parent" refer to
it.) All members of a group must share the same compute level (`compute_root`, or
the same `compute_at` site); mismatched compute levels are an error.

### How the fused nest is built

The group's stages are emitted into the shared nest in one **stage order**: a
single sequence that interleaves every stage of every member, each spliced in at
its own fuse level. This order is not computed up front — it is *discovered by the
emission itself* (step 2 below), one ready stage at a time, as the nest grows. The
body order, the produce nesting, and the legality rule all follow from this one
sequence. The procedure mirrors Halide's `build_pipeline_group`
([src_doc: compute_with/growth](src_doc/compute_with/growth.md); the
`[loopdoc-trace]` lines in any fused example's `debug_1` log print the member
order and the stage order directly).

Two granularities are in play, and keeping them apart is the whole trick of this
section: a fuse edge joins two *stages* (`f.s0` into `g.s1`), but the members are
ordered as whole *Funcs*. Read "Func A is a child of Func B" to mean *some* stage
of A fuses into a stage of B.

1. **Order the members (Funcs).** Topologically sort the Funcs with each child
   before its parent, breaking whatever the fuse edges leave unordered — several
   children of one parent, or one child's two parents — by the §6 realization
   order (the same name-then-visitation tie-break). Members have no
   producer/consumer dependency among themselves (a precondition) and no cyclic
   fuse edges (Legality), so the fuse edges are the only ordering constraint and
   the sort is well-defined. A chain `g.compute_with(f)`, `h.compute_with(g)` is
   deepest-child-first `h, g, f`. The last Func in this order — whatever the
   group's shape — is the **spine owner**. When one member is an ancestor of all
   the rest (children fused into one parent, or a chain) that ancestor is the
   spine owner; when none is — e.g. one child with two parents — it is simply
   whichever Func the tie-break places last (`h` in
   [examples/compute_with_two_parents.cpp](examples/compute_with_two_parents.cpp),
   members `f, g, h`). It has two roles below: the outermost `produce` (step 3),
   and the sole owner of the real shared loops (Loop ownership). This is the
   group's within-group realization order
   ([src_doc: compute_with/ordering](src_doc/compute_with/ordering.md)).
2. **Emit the stages, in a repeated sweep.** Walk the members in step-1 order and
   emit each member's stages in order (`s0`, `s1`, …) for as long as the next one
   is *ready*; when it is not, move on to the next member, and keep sweeping until
   every stage is placed. A stage is **ready** once all earlier stages of its own
   Func are placed and — if it is fused — its parent stage is placed. (So a member
   whose next stage is blocked is skipped this sweep and revisited later; its
   stage can then land after stages of members ordered *after* it — see "The two
   observable orders".) This emitted sequence is the body/compute order. Emit each
   ready stage by:
     * **unfused** (no fuse level — including every spine-owner / root stage):
       start its own loop nest, appended as a sibling;
     * **fused**: splice it into its parent stage's nest at the edge's `v`. They
       share the loops from the outermost down to `v`; the child's loops below `v`
       become siblings after the parent's body (if `v` is innermost, the bodies
       sit directly in the shared loop). Only the parent contributes the real
       shared loops — the child's own copies of every loop from the outermost down
       to `v` collapse to extent-1 *scheduling points*, all sitting at this one
       splice position. They print no `for`, but they are what lets
       `compute_at(child, …)` (and `store_at`/`hoist_storage`) name a site at the
       child's position rather than the parent's, with a surprising consequence
       once more than one loop is shared (Loop ownership).
3. **Wrap with `produce`/`consume`.** Wrap the finished body in a `produce` /
   `consume` for every member, nested in the reverse of the member order — so the
   spine owner is the outermost `produce`, and the `consume` blocks mirror it
   around the downstream consumer.

The basic case — `f`, `g` both `compute_root`, `x` split into `xo, xi`,
`g.compute_with(f, xo)`, `h = f + g` — has members `g, f` (spine owner `f` last)
and stage order `f.s0` (own nest; `g.s0` was blocked when `g`'s turn came), then
`g.s0` (spliced into `f.s0` at `xo`), giving
([examples/compute_with_split.cpp](examples/compute_with_split.cpp)):

```
produce f:                 # wrap: f (last realized) outermost, g inside
  produce g:
    for fused.y:           # shared loops, outermost down to the fused level xo
      for fused.xo:
        for xi:            # below v: each member's own loop, as siblings
          f(...) = ...     # parent body first
        for xi:
          g(...) = ...
consume f:
  consume g:
    produce h: ...
```

Because emission is per stage, a member's stages need not be consecutive: an
unfused stage of one member can land between two stages of another.
[examples/compute_with_update.cpp](examples/compute_with_update.cpp) fuses both
the init and the update stages (two shared nests in a row); the two-parent
example lands `f`'s two stages in different nests with `h`'s pure stage between
them — yet all of `f`'s stages still live under the single `produce f`, so §3's
"one `produce` per Func" holds even though "consecutive" does not.

### The two observable orders

A Func can come *before* another in the member order yet have a stage fall
*after* the other's stages in the stage order — because the member order ranks
whole Funcs (child before parent) while the stage order interleaves individual
stages (a parent's own stage emits before the children spliced into it). So with
`g.compute_with(f,y)`, `h.compute_with(f,y)` the member order is `g, h, f` (parent
`f` last) but the bodies run `f, g, h` (parent's stage first); the produce blocks
then nest `f, h, g` (member order reversed)
([examples/compute_with_three.cpp](examples/compute_with_three.cpp)). The
reverse-nesting holds even when the parent's name would sort it first
([examples/compute_with_parent_alpha.cpp](examples/compute_with_parent_alpha.cpp)),
and a chain's member order is deepest-child-first, not §6 name
([examples/cwtest_mixed_tile_factor.cpp](examples/cwtest_mixed_tile_factor.cpp)).

The sweep in step 2 is what makes the body order more than "each member's stages
at its slot": it matters whenever a member's next stage is blocked. In
[examples/cwtest_update_stage_diagonal.cpp](examples/cwtest_update_stage_diagonal.cpp)
the members order `f, g, h` (`f.s2` fuses into `g.s1`, `g.s1` into `h.s0`) and
`f.s1`, `g.s2` are unfused. Sweeping `f, g, h`:

* `f` emits `s0`, `s1`; `s2` blocks (its parent `g.s1` isn't placed yet).
* `g` emits `s0`; `s1` blocks (parent `h.s0` not placed), stalling `g` — `s2` is
  stuck behind it.
* `h` emits `s0`, `s1`, `s2` (all unfused); placing `h.s0` unblocks `g.s1`.
* second sweep — `g` emits `s1` (now ready; spliced into `h.s0`) then the free
  `s2`; `f` emits `s2` (spliced into `g.s1`).

Only the unfused stages start their own sibling nests, so the top-level body runs
`f.s0, f.s1, g.s0, h.s0`(with `g.s1, f.s2` spliced in)`, h.s1, h.s2, g.s2`. The
free `g.s2` lands last, after all of `h` — not "at `g`'s slot" — because `g`
stalled in the first sweep behind its fused `s1` and was not revisited until
`h.s0` freed it ([src_doc: compute_with/ordering](src_doc/compute_with/ordering.md);
verified via `[loopdoc-trace]`).

### Loop ownership: the `(child, v)` site

Step 2 noted that only the spine owner keeps real shared loops; every other
member's shared loops collapse to extent-1 scheduling points at its splice
position. This is about membership, not the parent/child role: in a chain
`f`→`g`→`h` the middle func `g` is `f`'s parent yet still collapses, because it is
not the spine owner — `f` splices into `g`'s collapsed slot, so `g`'s and `f`'s
bodies end up siblings at `g`'s position inside `h`'s real loops. The
**surprising** part appears once two or more loops are shared: all of such a
member's collapsed loops sit at the *same* place — its splice position at the fuse
point — so naming any of them as a site resolves there, no matter which loop you
named. A site `(non-spine-owner, v')` for a shared `v'` *above* the fuse level is
therefore not the same place as `(spine-owner, v')`, even though the two loops
were fused together.

[examples/human_compute_at_compute_with_child.cpp](examples/human_compute_at_compute_with_child.cpp)
makes this concrete: `parent`/`child` fuse at `y` (loops `z` outer, `y`, `x`), and
`g.compute_at(child, z)` realizes `g` inside `fused.y` at `child`'s slot (per-`y`),
whereas
[examples/human_compute_at_compute_with_child_no.cpp](examples/human_compute_at_compute_with_child_no.cpp)'s
`g.compute_at(parent, z)` realizes `g` under the real `fused.z` loop, above
`fused.y` (per-`z`). Same named loop `z`, different sites. (Why, with source +
trace: [src_doc: compute_with/member_sites](src_doc/compute_with/member_sites.md).)
Two consequences:

* A producer computed at `(member, v)` lands at that member's slot, and its
  legality is the ordinary §7 rule — the site must enclose every use of the
  producer; there is no special "name the parent" requirement. So naming a child
  is legal when the producer is used only within that child
  ([examples/compute_with_producer.cpp](examples/compute_with_producer.cpp)
  computes a shared producer at the parent; a child site is equally fine when its
  body encloses every use), and illegal when a use lies outside it — e.g. when the
  producer is read by another member, as in
  [examples/neg_compute_with_producer_at_child.cpp](examples/neg_compute_with_producer_at_child.cpp)
  (its `input` is read by both `f` and `g`, so the child's site doesn't enclose
  `f`'s use). This holds identically for `compute_at`, `store_at`, and
  `hoist_storage`. (A fused loop's bounds are the union over the members, so a
  producer there can keep a loop it would collapse at a plain `compute_at`; as
  always such elision is declared, not derived.)
* `micro_halide_collapses` keys on the loop's owner: collapse a shared loop by
  annotating the spine owner (any other member's shared loop is already an
  extent-1 scheduling point — not a printed `for` — so annotating it changes
  nothing), and a below-`v` loop on the member that owns it (which may differ
  between members).
  [examples/compute_with_at.cpp](examples/compute_with_at.cpp) collapses the
  shared `y` via the parent alone.

The same collapse drives a chain subtlety: a child's fuse level into its parent
should be **at or below the parent's own fuse level**. If a child fuses into a
non-spine-owner parent at a loop *above* that parent's fuse level, the loop is one
of the parent's collapsed dummies, so the child splices at the parent's slot and
the child's loops below `v` re-materialize as real loops nested inside the spine
owner's shared nest — recomputing the child redundantly (correct, just wasteful).
[examples/compute_with_chain_outer.cpp](examples/compute_with_chain_outer.cpp) is
this surprise (`f` fused outer than `g`);
[_inner](examples/compute_with_chain_inner.cpp) and
[_equal](examples/compute_with_chain_equal.cpp) are the well-behaved cases.

### Legality

`compute_with` has its own preconditions, separate from the general
compute_at rule:

* The two fused stages must have matching loop nests down to `v` — checked on the
  resulting dimension lists (§3), not the scheduling provenance: `v` must exist by
  name in both
  ([examples/neg_compute_with_mismatch.cpp](examples/neg_compute_with_mismatch.cpp)),
  and the count of loops from the outermost down to `v` must match (so `v` is at
  the same depth), paired up by name
  ([examples/neg_compute_with_dim_count.cpp](examples/neg_compute_with_dim_count.cpp)).
  The paired-up shared loops must also agree in kind — a `Var` fuses with a `Var`,
  an `RVar` with an `RVar` (the fuse level itself may be either: fusing two update
  stages at a shared reduction var is fine,
  [examples/compute_with_rvar.cpp](examples/compute_with_rvar.cpp)). In practice
  the kind follows from the name, since two stages rarely give the same name to a
  `Var` in one and an `RVar` in the other. Loops below `v` and all extents may
  differ — different `split` factors or matching `tile`s fuse fine
  ([examples/compute_with_tile.cpp](examples/compute_with_tile.cpp)), but a
  `reorder` that moves `v`'s depth does not.
* The fused Funcs must have no producer/consumer dependency
  ([examples/neg_compute_with_dependency.cpp](examples/neg_compute_with_dependency.cpp)).
* Two members may not fuse into **each other**: if any stage of `f` fuses into `g`
  and any stage of `g` fuses into `f`, the member order would have to put each
  before the other. Halide rejects it up front — `f.compute_with(g, …)` together
  with `g.update(0).compute_with(f.update(0), …)` errors "Found cyclic
  dependencies between compute_with of f and g". (This is the cross-Func direction
  cycle; the "stage order must exist" bullet below is the within-Func
  stage-index version.)
* All group members must share one compute level — the same `compute_root`, or the
  same `compute_at` site. This is not arbitrary: the whole group is injected as a
  single loop nest at that one compute level (one injection point), so every
  member's stages interleave there in one stage order. That is exactly what lets a
  Func's pure and update stages survive being fused into different parents: they
  still land in that one nest in stage order, and the Func's allocation (at its
  store level, which always encloses its compute level) spans both — so the pure
  stage's writes reach the update stage's reads. Two different compute levels would
  have no single injection point, so Halide rejects them
  ([examples/neg_compute_with_level_mismatch.cpp](examples/neg_compute_with_level_mismatch.cpp));
  [examples/compute_with_two_parents_at.cpp](examples/compute_with_two_parents_at.cpp)
  is the legal cross-parent case at a shared `compute_at(out, y)`. The rule is
  exactly compute-level equality — members may still have different store levels.
* The stage order (above) must exist — `compute_with` may not pin a Func's stages
  so that no consistent order can. Since a Func's own stages are forced into order
  (`s0` before `s1` …), as a Func's stages advance the parent-stage index they
  fuse into must be **non-decreasing, and may repeat only across consecutive fused
  stages** (no unfused stage in between). Two failure shapes: a *decrease* — fuse
  `f.s0` into `g.s1` but `f.s1` into `g.s0`
  ([examples/neg_cwtest_crossing_edges2.cpp](examples/neg_cwtest_crossing_edges2.cpp))
  — and a *repeat across a gap* — fuse `f.s0` and `f.s2` both into `g.s0` while
  `f.s1` is unfused
  ([examples/neg_cwtest_crossing_edges1.cpp](examples/neg_cwtest_crossing_edges1.cpp)):
  the unfused `f.s1` must sit strictly between `f.s0` and `f.s2`, yet both are
  pinned to the single stage `g.s0`, leaving nowhere to put it. Either way Halide
  rejects up front with "impossible to establish correct stage order" — checked
  per Func, per parent ([src_doc: compute_with/legality](src_doc/compute_with/legality.md)).

A producer's `compute_at` legality inside a fused group needs no new rule — it is
exactly §7's principle, the site must enclose every use of the Func, evaluated
against the post-fusion loop structure. Both "computed at a child" and "computed
at a loop that no longer spans a use fusion moved into another member's body" are
just that one check failing.


### Out of scope (bounds-only, invisible here)

`compute_with` also takes `LoopAlignStrategy` arguments controlling how the two
stages' iteration domains are aligned in the shared loop, and Halide inserts guard
`if` predicates when the fused stages have different extents. These affect loop
bounds and guards, not the `for`/`produce`/`consume` structure, so — like all
bounds detail — they do not appear in the canonicalized nest and are not modeled
here.

### Known issue: inconsistent `tile` across a fused group

Avoid fusing on a level `v` that is a `tile`/`split` *result* in one member but
reached by a different (or no) `tile` in another member of the same group — i.e.
the members arrive at `v` through inconsistent loop transforms. This is a genuine
Halide bug, not a structural quirk: it can generate out-of-bounds accesses
([Halide #4751](https://github.com/halide/Halide/issues/4751)). Other
`compute_with` shapes — multi-child groups, chains, members with differing
extents, even differing fuse levels — compute *correct* results; this
inconsistent-tiling case is the one to steer clear of. (Not modeled here — no
example exercises it.)


---

## 15. Putting the algorithm together (how the nest is built)

The whole loop nest follows from the rules above, assembled into one procedure:

1. **Force the output to `root`** — the Func you call `print_loop_nest()` on is
   always computed at the outermost level (§5, §6).

2. **Compute the realization order** — topologically sort the pipeline so every
   producer precedes its consumers, breaking ties by name (§6). All Funcs remain
   in this order so they can pass dependencies along; a **pure inline** Func
   (§4) is never realized and drops out of the steps below (§5), but a non-pure
   inline Func *is* realized (§11) and keeps its slot. An `rfactor` intermediate
   (§12), and any `in`/`clone_in` wrapper or clone (§13), are likewise ordinary
   Funcs in this order — a wrapper/clone sits between the wrapped Func and the
   consumers it was created for — with whatever schedule each was given. A
   **fused group** (§14) is ordered as a *unit*: the sort treats the group as one
   node (placed by the realization order of the whole group), and **within** the
   group the members take consecutive slots in §14's within-group order — a
   *second* topological sort, over the group's **fuse edges** (each child before
   the parent it fuses into, §6 tie-break), since the members have no
   producer/consumer dependency to order them. The whole group realizes as one
   block (§14).

3. **Give each realized Func a site.** A *realized* Func is any Func that is
   **not** a pure-inline Func — i.e. it gets its own `produce` block. Each goes
   to a site:
     * `compute_root` Funcs and the output form the **top-level chain**, kept in
       realization order;
     * a `compute_at(g, v)` Func is **filed under** site func `g`'s loop over `v`. If
       several Funcs are filed at the same `(g, v)`, they keep realization order;
     * a **non-pure inline** Func (§11) is filed at the innermost loop enclosing
       each of its uses — like a `compute_at` resolved independently per use site.
     * a **fused group** (§14) is filed *once*, as a unit, at the **one compute
       level its members all share** (§14 requires they match); the individual
       members take no site of their own and are interleaved there when the group
       is emitted (step 4).
   Each realized Func also has a **store level** (§8), defaulting to its compute
   level. The level var `v` is a dimension of the site func's **(possibly transformed)
   dimension list** for the relevant stage (§3, §9): `split`/`fuse`/`reorder`/
   `tile` have already rewritten that list before this step.

4. **Emit outside-in.** A *realized item* is a single Func **or** a whole fused
   group (§14) — realization order (step 2) and the placements of step 3 are over
   these items, a lone Func being a one-member group. Begin at the **top-level
   chain** (the `compute_root` items and the output, in realization order) and
   emit each item; emitting an item recurses, so the nest grows inward.

   **Emitting an item** writes its `produce`/`consume` wrapper around its loop
   nest(s):
     * a **single Func** `f`: `produce f`, then `f`'s loop nest(s), then
       `consume f` wrapping everything that follows. A Func with update
       definitions (§3) emits **one loop nest per stage**, in stage order, all
       inside that single `produce f` (no `consume` between stages).
     * a **fused group**: §14's growth procedure — the members' stages injected in
       stage order into one shared nest, the whole wrapped by a `produce`/`consume`
       for **every** member (last-realized outermost). (Verified against Halide's
       `build_pipeline_group`; see §14 and
       [src_doc: compute_with/growth](src_doc/compute_with/growth.md).)

   **A stage's loop nest** is built by walking that stage's dimension list (after
   its §9 transforms; an update stage's list also carries its `RVar`s) from the
   outermost dimension inward. At each dimension:
     * if the dimension was declared **elided** (§7), skip its `for` line but keep
       the level as a valid injection site;
     * **open a `store h:` node** for any item `h` whose store level is this level
       while its compute level is deeper (§8); everything below falls inside it.
       This is *per item*: in a fused group each member whose store level is outer
       gets its own `store` node here, and the common default — store level equal
       to the (shared) compute level — doesn't print anything. When several items
       open a `store` node at this same level they **nest** (not siblings), in the
       same order as their `produce` blocks — so the order *does* depend on whether
       they share a fused group: ordinary items nest in realization order
       (first-realized outermost, §6 tie-break by name), while members of one fused
       group nest in the group's order (last-realized / parent outermost, §14);
     * **inject** the items filed at this `(f, stage, dim)` level (step 3), each
       emitted by *this same procedure* (the recursion), its `consume` wrapping
       the rest of the body;
     * descend inward, bottoming out at the leaf `f(...) = ...`.

   "Inject an item" is the single recursive step, so an item filed inside an item
   filed inside a third nests accordingly. `store_root()` is the case where an
   item's `store` node opens at the very outermost level, around the whole nest.

[examples/many_compute_root.cpp](examples/many_compute_root.cpp) puts the core
pieces together: `f1`, `f2`, `f3` are `compute_root` and so form the top-level
chain in that order; `f4` is `compute_at(output, y)` and is injected under the
output's `y` loop; and `clamped` is inlined, so it never appears.
[examples/many_store_at.cpp](examples/many_store_at.cpp) exercises the storage
level: a `store_root` Func, a `store_at`-at-an-outer-loop Func (distinct
`store`/compute levels), and a Func whose store level equals its compute level.

---

## Source-level evidence

The compiler-level justification for the above — where outputs are forced to
`compute_root`, where the produce/consume nodes and realization order come from,
how update stages become sibling loop nests, and why extent-1 loops disappear —
is in the **`src_doc/`** set, organized by topic and indexed in
[src_doc/README.md](src_doc/README.md):
[overview](src_doc/overview.md) (root/inline defaults, realization order,
produce/consume), [compute_at and loops](src_doc/compute_at_and_loops.md),
[storage](src_doc/storage.md) (`store_at`/`hoist_storage`),
[transforms](src_doc/transforms.md) (`split`/`fuse`/`reorder`/`tile`),
[update definitions](src_doc/update_definitions.md),
[rfactor](src_doc/rfactor.md), [in/clone_in](src_doc/in_clone_in.md), and
**compute_with** (split by topic: [fused_groups](src_doc/compute_with/fused_groups.md),
[growth](src_doc/compute_with/growth.md),
[member_sites](src_doc/compute_with/member_sites.md),
[ordering](src_doc/compute_with/ordering.md),
[legality](src_doc/compute_with/legality.md)).
