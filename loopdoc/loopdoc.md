# Halide scheduling and the loop nest

This document explains how Halide turns a *scheduled pipeline* into a *loop
nest*, at the level of detail needed to predict the output of
`Func::print_loop_nest()` by hand. It is built up holistically: each section
adds to a single mental model rather than describing an isolated feature.

The core model (§§1–10, §16) is self-contained. The heavier features
(`rfactor`, `in`/`clone_in`, `compute_with`, `specialize`, loop types) are
summarized in §§11–15 and §17 and treated in full in the linked `detail/`
documents — see [Detail documents](#detail-documents) at the end.

---

## 1. The programming model

A Halide program is built in two separable parts:

1. **The algorithm** — *what* each pixel's value is. You declare `Func`s and
   define them as pure mathematical functions of their argument `Var`s, plus
   optional **update definitions** that refine those values (§3).
2. **The schedule** — *when and where* each value is computed and stored. This
   is expressed by scheduling directives (`compute_root`, `compute_at`, …)
   attached to each `Func` (§§5–9).

Crucially, scheduling preserves **functional equivalence**, i.e., for a given
algorithm and input state, the output state will always be the same regardless
of scheduling decisions, except for changes due to roundoff or overflow.
The schedule only changes the order of computation, the amount of redundant recomputation,
and the temporary storage used.

This document is only about how the program maps to a loop nest.

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
      listed is the *innermost* loop — see §3). Each dimension also carries a
      **loop type** (`serial` by default, else `parallel`/`vectorized`/`unrolled`/
      `gpu_block`/`gpu_thread`/`gpu_lane`) and, for the GPU types, a **device**
      (`DeviceAPI`) — set per stage by `parallel`/`vectorize`/`unroll`/`serial`
      and the `gpu_*` directives (§17, [detail](detail/fortype.md)), and carried
      along by `split`/`fuse`/`reorder`. Each stage also has **its own left-hand-side
      index expressions and right-hand-side value expressions** — the "algorithm"
      of that stage. The LHS/RHS are *seeded* by the definition you wrote, but
      they are **part of the mutable, per-stage scheduling state**, not a
      separate immutable "algorithm": it may be rewritten by `rfactor` (§12,
      [detail](detail/rfactor.md)).
      This modifies the LHS/RHS specified by the original algorithm,
      but in a way that preserves functional equivalence.
    * the set of **other Funcs it reads from** (its *producers*), derived from
      the (current, possibly rewritten) right-hand sides (and update
      left-hand-side indices) of all its stages,
    * its **compute level** and **store level** — `inline` (the default),
      `root`, or `at(site func, var)` — set by the schedule (§§5–8). These apply to
      the Func as a whole (all stages move together).
    * a per-stage **fuse level**, set by `compute_with` (§14,
      [detail](detail/compute_with.md)) and *empty* by
      default. A stage with a fuse level is not computed in its own loop nest but
      **interleaved into another stage's**, the two sharing their outer loops;
      the connected set of stages so tied together forms a **fused group**.
      Unlike the compute/store level, this is set *per stage*, not whole-Func.
    * a per-stage, ordered list of **specializations**, set by `specialize` (§15,
      [detail](detail/specialize.md)).
      This is empty by default, with each specialization being a (condition, stage)
      pair. Each specialization becomes a conditional variant of the stage's
      loop nest, constructed from the child stage's schedule. If no condition
      is true at runtime, then the fallback case constructed from the owner stage's
      schedule is executed, unless `specialize_fail` is used, which converts
      the fallback case to a runtime error.
      Each specialization stage carries its own per-stage state; therefore,
      they can be freely modified with scheduling directives like `rfactor`
      without the change propagating back to the owner stage.

* **`ImageParam`** — an input buffer. It is a *leaf*: it is never computed and
  never appears in the loop nest. A Func that reads an `ImageParam` simply has
  one fewer producer to realize; the buffer is assumed already present.

* **`Expr`** — a value expression appearing on the right-hand side of a
  definition (the RHS state above; a stage's RHS is a list of these — a `Tuple`
  Func has several). For loop-nest purposes the only thing that matters about an
  Expr is *which Funcs it reads*: that is what wires up the producer/consumer
  graph. (A directive that rewrites a stage's LHS/RHS — `rfactor`, §12 —
  therefore changes which producers a stage has.)
  Pointwise arithmetic, constants, `cast<T>(...)`, and the like are invisible
  in the loop nest — they live *inside* the `f(...) = ...` leaf line.
  Exception: 1-iteration loops are removed; this relies on bounds inference,
  which depends more deeply on the contents of an Expr. See §7 (and only for
  non-GPU loops — a 1-iteration GPU loop survives, §17).

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
for <var>:          # a loop over one dimension (changing ForType, §17, replaces the "for")
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

This document predicts loop *structure*, not two things Halide also prints that
are incidental to it: the exact **loop-variable names** (compound, carrying
Halide's internal `split`/`rfactor` naming lineage plus a global counter — not
reproducible by hand) and constant **loop bounds** (`for x in [0, 7]`, which
follow from bounds inference, out of scope here). What *is* significant: the
produce/consume nesting, the number and nesting of `for` loops, their order, and
their type (`parallel`, `vectorized`, `gpu_block`, …; §17).

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

`RVar` loops print like any other (`for r in [min, max]`); the constant bound is
ignored (§2), so they read as plain `for`. A `k`-dimensional `RDom`
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

`f.compute_inline()` is the **inverse**: it resets `f`'s compute level back to
`inlined`, the default — it is literally `compute_at(inlined())`. Its purpose is
to **undo** a previous `compute_root`/`compute_at`, after which `f` behaves as if
never scheduled: a pure `f` is substituted into its callers and vanishes from the
nest (§5); a non-pure `f` is realized at its innermost use (§11). It resets *only*
the compute level — any recorded `split`/`fuse`/`reorder` stays. For a **pure**
inline Func that is moot (it vanishes; Halide warns *"meaningless to split …
scheduled inline"* if you transform one). But a **non-pure** inline Func is still
realized (§11), so a transform on its stages genuinely takes effect on those
realized loops — a `split` inner loop survives
([examples/compute_inline_split_nonpure.cpp](examples/compute_inline_split_nonpure.cpp)),
a `fuse` produces the fused loop
([examples/compute_inline_fuse_nonpure.cpp](examples/compute_inline_fuse_nonpure.cpp)).
A **store or hoist level may not be left set on an inlined Func**, even when
reached by an inline override
([examples/neg_compute_inline_leftover_store.cpp](examples/neg_compute_inline_leftover_store.cpp),
[examples/neg_compute_inline_leftover_hoist.cpp](examples/neg_compute_inline_leftover_hoist.cpp));
see §8 Legality.

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

### Realization order: tie-breaking, inline Funcs, fused groups

That topological order is not unique: where the graph leaves a consumer's
independent producers unordered, Halide picks a **deterministic** order. Two facts
complete the picture — every Func is a node in the graph *including* ones that end
up inlined (an inline Func still transmits the dependencies of what it reads,
keeping its producers ahead of its consumers), and a **fused group** (§14) is
placed as a single node. The exact walk (a post-order DFS from the output), the
per-consumer edge-label tie-break and its first-visitation index, and the
fused-group treatment are in
[detail/realization_order.md](detail/realization_order.md).

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

**Predicting exactly which dimensions collapse requires bounds inference**, which
is out of scope for this document: which loops have extent 1 depends on the actual
index arithmetic, not on the schedule structure. The loop *structure* itself —
produce/consume placement, ordering, and which loops exist — is fully determined
by the schedule (§§5–9); only *which* of those loops then collapse to points is
the bounds-dependent part left underivable here.

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
  elision is why loop collapse must be reasoned about per stage.)

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

The whole rule is one principle: **the chosen level must enclose every read of
`f`.** `f` realized inside site func `g`'s `v` loop can only feed reads that lie
inside some `g.*.v` loop, so the schedule is legal exactly when every read of `f`
— in *any* stage of `g`, and including reads reached *indirectly* through Funcs
`g` calls — sits inside that loop family. Put loosely: don't realize `f` where a
consumer won't be able to read it, and don't realize it at a `g`/`v` that no
reading stage of `g` actually has. The two ways to be illegal are the two ways
that fails: naming a `v` that some reading stage lacks (nowhere to inject `f`),
and `f` being read *outside* `g` — a different consumer, or `g`'s own outer scope
— where the only enclosing level is `root` (the classic case the wrapper Funcs of
§13 exist to repair). This single check is simply re-evaluated against the
post-transform, post-fusion loop nest; no later directive adds new legality cases.

Full rules — the loop-level/family model, indirect reads, the per-stage
treatment, and the worked negative examples — are in
[detail/compute_at_legality.md](detail/compute_at_legality.md). The illegal cases
there are rejected with an error rather than producing a loop nest.

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
affect constant bounds, which this document ignores). The single visible effect on
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
  ([examples/neg_store_at_inlined.cpp](examples/neg_store_at_inlined.cpp)). This
  is checked purely on the compute level being `inlined`, so it holds even for a
  **non-pure** Func — although such a Func *is* realized (at its innermost use,
  §11), that realized-inline default still does not carry a store level; you must
  give it an explicit `compute_at`/`compute_root` first. (So `compute_inline()`,
  §6, and a store/hoist level are mutually exclusive.)
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
inner loop the constant bound `[0, factor-1]`, but this document ignores both
loop names and constant bounds — so the only structural signal of a
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
> document models it.** Because this document ignores loop-variable names
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
visible once loops carry distinct *types* — `vectorize`/`parallel`/`unroll` (§17)
— which this document *does* track.)

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

You do not need to predict Halide's exact Func *names*. Many Funcs are
auto-generated — `in`/`clone_in` wrappers and clones (§13), `rfactor`
intermediates (§12), boundary-condition helpers (below) — with internal suffixes
that are not reproducible by hand. What matters for the loop nest is its
*structure* and the set of **distinct Funcs** it realizes: how many there are and
how they nest. Two naming facts that affect that Func set:

* `BoundaryConditions::repeat_edge(input)` and friends create a **real** wrapper
  Func (printed `repeat_edge`, dimensions auto-named `_0, _1, _2, …`); it is
  realized if scheduled (in `box_blur` it is `compute_root`), and its dimension
  count matches the input's.
* `cast<T>(...)` does **not** create a Func — it is an Expr operation that
  stays inside the leaf line of whatever definition contains it.

---

## 11. Inlined non-pure Funcs (the deferred default)

§§5–10 assumed every inline Func is pure, and therefore non-realized (§4). The one
leftover case: a **non-pure** Func (one with update definitions, §3) left at the
default **inline** level cannot be textually substituted (a reduction is not an
expression), so Halide *realizes* it — at the innermost loop enclosing **each**
use, recomputed there every iteration. When `f` is read at a single depth this is
exactly `f.compute_at(consumer, v)` at that loop, which is why the rest of this
document can treat the non-pure inline default as "a default `compute_at` at the
innermost use." It genuinely exceeds any one `compute_at` only when `f` is read at
*different depths in different stages* of a consumer — the inline level places `f`
at each use's own enclosing loop, independently per use. It is uncommon and rarely
the fast choice. Full treatment and the multi-depth case:
[detail/inline_nonpure.md](detail/inline_nonpure.md).

---

## 12. `rfactor`: factoring an associative reduction into a new Func

`f.update(i).rfactor(...)` parallelizes a reduction by splitting it into
independent partial reductions plus a final merge. Unlike the other directives it
changes the algorithm's *structure*: it **creates a brand-new intermediate Func**
— a real pipeline node, realized in the order of §6 — and **rewrites the update
stage** it was called on into the merge that reads that intermediate. That rewrite
is a functional-equivalence-preserving edit of the stage's left- and right-hand
sides (§1); the intermediate becoming a producer of `f` is precisely the RHS now
reading it.

The intermediate is an ordinary multi-stage Func returned to you and scheduled
like any other — by default it takes the non-pure inline default (§11), so you
normally give it `compute_root`/`compute_at`. Applied through a `specialize`
branch handle (§15), `rfactor` rewrites only that branch. Full construction, the
LHS/RHS-edit model, scheduling the intermediate, and the legality/limits (incl.
factoring a split `RVar`) are in [detail/rfactor.md](detail/rfactor.md).

---

## 13. `in` and `clone_in`: wrapper and clone Funcs

Both create a **new, separate Func** that a chosen set of consumers read instead
of the original. `f.in(g)` is an identity **wrapper** (`f_in_g(args) = f(args)`)
that reads `f`'s stored result; `f.clone_in(g)` is an independent **clone** that
*recomputes* `f`'s work — a copy of `f`'s whole definition and schedule as they
stand at the call (so a default schedule only if `f` is still unscheduled). Both
start non-realized and appear only once given a compute level (§4–§5). Forms:
`f.in(g)`, `f.in({g1, g2, …})`, and a global `f.in()`. The classic use is
repairing the "two consumers force `f` to `root`" situation (§7,
[neg_compute_at_two_consumers.cpp](examples/neg_compute_at_two_consumers.cpp)) by
giving each consumer its own wrapper.

**Recommendation: pass only Funcs that read `f` *directly*.** `f.in(g)` /
`f.clone_in(g)` do not necessarily modify `g`. They run a recursive search down
the *current* call graph and redirect the **first direct caller of `f`** on each
path — a shared Func, with the target set frozen at call time and blind to other
pending wraps. When `g` reaches `f` only through intermediates, that produces a
family of surprises: redirecting Funcs you never named, order-dependent collisions
between two wraps, pins going stale after an `rfactor`, and a clone feeding a
consumer you didn't request. Passing direct consumers avoids all of them. The full
pin-resolution algorithm and each surprise (with examples) are in
[detail/in_clone_in.md](detail/in_clone_in.md).

Two facts hold **even when you follow the recommendation:**

* **A clone shares `f`'s inputs.** The deep copy duplicates `f` but reads the
  *same* producers `f` reads, so a producer `p` that `f` reads is now read in two
  places (`f` and the clone) — the only level enclosing both is `root`, making
  `p.compute_at(f, x)` illegal
  ([neg_clone_shared_callee.cpp](examples/neg_clone_shared_callee.cpp)). Clone the
  inputs too to give the clone private ones. (A clone can also *delete* `f`: if
  every reader of `f` is redirected, `f` becomes unreachable and drops out — a
  wrapper, which reads `f`, never does this.)
* **A Func can be cloned only once.** A second, *distinct* clone or wrap on an
  already-wrapped Func aborts (`copied_func.defined()` in
  `FuncSchedule::deep_copy`) — a still-open upstream bug
  ([#6476](https://github.com/halide/Halide/issues/6476),
  [#3661](https://github.com/halide/Halide/issues/3661)), undocumented in the API.
  Repeating the *same* `f.clone_in(a)` is fine (it returns the first clone);
  `f.in()` never deep-copies and is exempt.

---

## 14. `compute_with`: fusing stages into a shared loop nest

`b.compute_with(a, v)` interleaves two stages that would otherwise run in separate
nests into **one shared loop nest**, sharing their loops from the outermost down
to `v`. It creates no Func and changes no value — it only reshapes loops. The
Funcs tied together (directly or transitively) by these per-stage **fuse edges**
form a **fused group**, realized as a unit.

The fused group is the general form of the unit §16 emits, so it is worth naming
even in the simple case: **a single realized Func with no `compute_with` is just a
one-member fused group** — one loop nest. A multi-member group is placed once in
realization order (as a single contracted vertex, §6), and its members' stages
interleave into the shared nest in one **stage order** (a parent's own stage
before the children spliced into it). Only one member — the *spine owner* — keeps
the real shared loops; every other member's shared loops collapse to scheduling
points at its splice position. All members must share one compute level.

Member ordering, the two observable orders (whole-Func member order vs. per-stage
body order), the `(child, v)` loop-ownership subtlety, and the full legality rules
(matching loop nests down to `v`, no producer/consumer dependency, one shared
compute level, a consistent stage order) are in
[detail/compute_with.md](detail/compute_with.md).

---

## 15. `specialize`: conditional schedule variants

`f.specialize(cond)` gives a definition a **conditional schedule variant**: at run
time `cond` selects a specialized loop nest, otherwise a fallback runs. It is per
definition — the pure stage or a specific update stage — and each specialization
forks **its own copy of the whole definition** (schedule *and* LHS/RHS, §1). So
directives on the returned handle (including `rfactor`, §12) affect **only that
branch**, while directives on `f` *after* the call affect the fallback. Repeated
calls on the same handle add sibling `if / else if` arms; calling `specialize` on
a returned handle nests inside that branch, so specializations form a tree.

`print_loop_nest` prints no conditions or `if`/`else` markers — it walks **every**
branch, so the branch nests appear **concatenated as sibling subtrees under one
`produce`**, in declaration order with the fallback last (`specialize_fail` leaves
no fallback nest). A producer computed at a loop of a specialized consumer is
injected into each branch independently, resolved against that branch's own loop
nest. What is *not* possible through scheduling: computing a producer differently
per consumer branch. Full rules — the specialization tree, identical-branch merge,
per-branch producers, the fragile `select`-pruning workaround, and legality
(including that a `compute_with` caller may have no specializations) — are in
[detail/specialize.md](detail/specialize.md).

---

## 16. Putting the algorithm together (how the nest is built)

The whole loop nest follows from the rules above, assembled into one procedure:

1. **Force the output to `root`** — the Func you call `print_loop_nest()` on is
   always computed at the outermost level (§5, §6).

2. **Compute the realization order** — order the pipeline so every producer
   precedes its consumers (the topological sort of §6; the exact DFS and tie-break
   are in [detail/realization_order.md](detail/realization_order.md)). All Funcs remain
   in this order so they can pass dependencies along; a **pure inline** Func
   (§4) is never realized and drops out of the steps below (§5), but a non-pure
   inline Func *is* realized (§11) and keeps its slot. An `rfactor` intermediate
   (§12), and any `in`/`clone_in` wrapper or clone (§13), are likewise ordinary
   Funcs in this order — a wrapper/clone sits between the wrapped Func and the
   consumers it was created for — with whatever schedule each was given. A
   **fused group** (§14) is a single contracted vertex here
   ([detail/realization_order.md](detail/realization_order.md) "Fused groups: one
   contracted vertex"):
   the group is placed as a unit, then **within** the group the members take
   consecutive slots in §14's within-group order (a topological sort over the
   group's **fuse edges**, child before the parent it fuses into, §6-ranked among
   what the fuse edges leave unordered). The whole group realizes as one block.

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
   outermost dimension inward. (If the stage's definition carries
   **specializations** (§15), this produces **one nest per branch** — each branch
   walking its own forked dimension list — emitted back to back inside the single
   `produce`, specialization branches first and the fallback last; the steps below
   describe building one such nest.) At each dimension:
     * if the dimension is **elided** (its extent is 1, §7), skip its `for` line but
       keep the level as a valid injection site (a 1-iteration loop elides only when
       it is not a GPU loop, §17);
     * otherwise emit the loop line with the dimension's **type token** (§17) —
       `for` by default, else `parallel`/`vectorized`/`unrolled`/`gpu_*` with any
       `<device_api>` suffix;
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

## 17. Loop types (`ForType`): `serial`, `parallel`, `vectorized`, `unrolled`, GPU

Independently of the loop *structure* the directives above build, each loop
carries a **type** that becomes the first token on its loop line: `for` (serial,
the default), `parallel`, `vectorized`, `unrolled`, or the GPU types
`gpu_block`/`gpu_thread`/`gpu_lane` (which additionally print a `<device_api>`
suffix). The type is a per-dimension property (§1, §3):
`parallel(v)`/`vectorize(v)`/`unroll(v)`/`serial(v)` set it in place; the factor
forms (`vectorize(v, n)`, `parallel(v, n)`) `split` first and type **one** of the
two halves (vectorize/unroll the inner, parallel the outer); the `gpu_*` family
sets the type plus a device. The type **rides the dimension** through
`split`/`fuse`/`reorder`, which is the second way a `reorder` becomes observable
(§9): the type token is significant, whereas two untyped serial loops are
indistinguishable.

Full directive list and overloads, the split-half asymmetry, the GPU specifics
(device suffix, `gpu_tile`/`gpu_single_thread`, why GPU legality is out of scope),
the type/device independence, and the extent-1-collapse-unless-GPU rule are in
[detail/fortype.md](detail/fortype.md).

---

## Detail documents

Several features above are summarized here and treated in full in `detail/`, so
the main document stays a readable overview. Each detail doc's section references
(`§N`) point back to this file.

* [detail/realization_order.md](detail/realization_order.md) — the realization-order
  DFS, its tie-break and first-visitation index, and fused groups as one node (§6).
* [detail/compute_at_legality.md](detail/compute_at_legality.md) — the full
  `compute_at` legality rules and the loop-level/family model (§7).
* [detail/inline_nonpure.md](detail/inline_nonpure.md) — inlined non-pure Funcs,
  including the multi-depth case (§11).
* [detail/rfactor.md](detail/rfactor.md) — `rfactor` construction, the LHS/RHS
  edit, and scheduling the intermediate (§12).
* [detail/in_clone_in.md](detail/in_clone_in.md) — `in`/`clone_in` pin resolution
  and every transitivity surprise (§13).
* [detail/compute_with.md](detail/compute_with.md) — fused-group member order,
  loop ownership, and legality (§14).
* [detail/specialize.md](detail/specialize.md) — the specialization tree,
  per-branch producers, and `select`-pruning (§15).
* [detail/fortype.md](detail/fortype.md) — the full loop-type/GPU rules (§17).
* [detail/micro_halide.md](detail/micro_halide.md) — the test apparatus these
  docs were validated against (explains the annotations and includes you'll see
  in the example programs; not about Halide itself).

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
[legality](src_doc/compute_with/legality.md)), and
[specialize](src_doc/specialize.md).
