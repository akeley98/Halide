# Halide scheduling and the loop nest

This document explains how Halide turns a *scheduled pipeline* into a *loop
nest*, at the level of detail needed to predict the output of
`Func::print_loop_nest()` by hand. It is built up holistically: each section
adds to a single mental model rather than describing an isolated feature.

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
      listed is the *innermost* loop — see §3), **and its own left-hand-side
      index expressions and right-hand-side value expressions** — the "algorithm"
      of that stage. The LHS/RHS are *seeded* by the definition you wrote, but
      they are **part of the mutable, per-stage scheduling state**, not a
      separate immutable "algorithm": it may be rewritten by `rfactor` (§12).
      This modifies the LHS/RHS specified by the original algorithm,
      but in a way that preserves functional equivalence.
    * the set of **other Funcs it reads from** (its *producers*), derived from
      the (current, possibly rewritten) right-hand sides (and update
      left-hand-side indices) of all its stages,
    * its **compute level** and **store level** — `inline` (the default),
      `root`, or `at(site func, var)` — set by the schedule (§§5–8). These apply to
      the Func as a whole (all stages move together).
    * a per-stage **fuse level**, set by `compute_with` (§14) and *empty* by
      default. A stage with a fuse level is not computed in its own loop nest but
      **interleaved into another stage's**, the two sharing their outer loops;
      the connected set of stages so tied together forms a **fused group**.
      Unlike the compute/store level, this is set *per stage*, not whole-Func.
    * a per-stage, ordered list of **specializations**, set by `specialize` (§15).
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

### Realization order in detail

Picture the pipeline as a directed graph: **nodes are Funcs**, and each node has
an **out-edge to every Func it reads** (consumer → producer). Every Func is a
node, including those that end up inlined — inlining is decided later (§5), and
keeping an inline Func as a node is what lets it **transmit dependencies** (inline
`b` reading rooted `a` keeps `a` ahead of everything that reads `b`; in
`box_blur`, `output` inlines `blur_y`→`blur_x`→`input_16`, so `input_16` precedes
`output`).

**The order is the post-order of a depth-first walk from the output(s)**, with
one shared *visited* set: at each node, descend its out-edges, then append the
node *after* its whole subtree; a node already visited is skipped. Two properties
fall straight out:

- a producer is appended before the consumer that descended into it, so **every
  producer precedes all its consumers**;
- a shared producer is reached by several paths but appended **once**, on the
  first — **realized once, ahead of every reader**
  ([examples/diamond_root.cpp](examples/diamond_root.cpp)). (Reaching a node that
  is visited but not yet appended is a back-edge = dependency cycle = error.)

The name never reorders the graph globally. A producer reachable only through an
alphabetically-*later* sibling realizes *after* that sibling's whole subtree even
if its own name sorts earlier:
[examples/realization_order_dfs.cpp](examples/realization_order_dfs.cpp) yields
`mid, f, a, out` — **`a` after `f` despite `"a" < "f"`** — because `a` is
reachable only behind `keep` in `out`'s out-edges.

#### The one degree of freedom: the order of a node's out-edges

The walk's only choice is the order in which it descends a node's out-edges (its
independent producers). Give each edge a **label** — the key of the producer it
points at — and the walk descends a node's out-edges in **label order**. The label
is:

> **prefix**, then **first-visitation index**, then **full name** — where the
> prefix is the name with any `$n` uniqueness suffix and trailing digits removed.

This ranks only *one consumer's* producers; it is not a global sort, and not the
left-to-right order of the defining expression
([examples/tiebreak_realization_order.cpp](examples/tiebreak_realization_order.cpp):
`a2d` before `b1d` though written `b1d(x) + a2d(x, y)`). For an ordinary edge the
label is just the target Func's key, so you can think "sort by target"; keeping it
as an edge *label* rather than a property of the target vertex matters only for
`compute_with`, below.

The middle field, **first-visitation index**, is a structural stamp (a separate
pre-order DFS, detailed next) — *not* a name. It is the field that actually
settles ties: when two producers share a prefix
([examples/tiebreak_visitation_order.cpp](examples/tiebreak_visitation_order.cpp):
two `b`-prefixed producers go by which is *visited* first, not alphabetically),
and *especially* when they share a full name — two `rfactor` intermediates both
printed `g_intm`, so prefix and name tie and first-visitation is the **only**
deciding field. This same ranking orders sibling producers filed at any single
`compute_at` level, not just root (§7).

#### First-visitation index

First-visitation order is a *pre-order* depth-first walk from the output(s),
separate from the realization walk: on reaching a Func, **stamp it with the next
index the first time it is seen**, then descend into the Funcs it calls, skipping
any already stamped. "The Funcs it calls, in order" means the calls across the
Func's whole definition, in this order:

- **Stages first-to-last** — the pure (init) definition, then each update stage in
  order (§3), so a producer read only in a later update is stamped later.
- **Within a stage, right-hand side before left-hand side** — calls in the *value*
  expressions (the RHS, what the stage computes) before calls in the *index*
  expressions (the LHS, where it stores). A Func read **only on the LHS** — a
  data-dependent scatter index, e.g. `hist(idx(x)) += 1` — is still visited and
  still gets an index; it is just stamped *after* that stage's RHS reads. (It is a
  genuine producer: `idx` must be computed before the stage can run, so it needs a
  slot like any other.)
- **Then the stage's `specialize` branches**, in declaration order, recursively
  (§15) — so a producer read only in a branch is stamped after the base
  definition's reads of the *same* stage.

(This mirrors the compiler's own definition walk — predicate, then values, then
args, then specializations — in `DefinitionContents::accept`.)

#### Fused groups: one contracted vertex (forward reference: `compute_with`, §14)

Because the tie-break lives on the **edge label**, `compute_with` (§14) is a plain
graph operation: **contract the group's members into a single vertex**. Contraction
in a *multigraph* keeps every edge — it never merges or relabels them — so the
group vertex has:

- **out-edges** = the union of the members' out-edges (to the members' producers),
  labels intact — so the group is realized once, after everything *any* member
  reads;
- **in-edges** = the union of the members' in-edges (from consumers), **each still
  carrying the label of the member it originally pointed at**.

Those preserved in-edge labels are the whole subtlety, and they are ordinary
multigraph edges — not a "half-collapse": a consumer that read member `a` has an
edge into the group labelled with `a`'s key, a consumer that read member `z` has
one labelled with `z`'s key. So the group vertex has **no key of its own** — where
it sorts among a given consumer's other producers depends on *which member that
consumer read*, i.e. on the edge label. Two consequences, both ordinary
labelled-graph facts:

- the group is one vertex, so it precedes every consumer of any member and follows
  every producer of any member
  ([examples/fused_group_consumer_interleave.cpp](examples/fused_group_consumer_interleave.cpp));
- flipping which member a consumer reads relabels that consumer's edge into the
  group, moving the group relative to the consumer's other producers
  ([examples/fused_group_edge_keyed_tiebreak.cpp](examples/fused_group_edge_keyed_tiebreak.cpp)).

§14 covers the group's internal structure; for realization order it is exactly
this one contracted vertex, edges and labels preserved.

<!-- MICRO GAP — failing: fused_group_consumer_interleave, fused_group_edge_keyed_tiebreak.
     micro places a fused group at its LAST member's slot in the flat per-Func
     realization order. That is neither the union-of-members out-edges (so it can
     fall AFTER a consumer of a member -> fused_group_consumer_interleave) nor the
     member-keyed in-edge (so it ignores which member a consumer read ->
     fused_group_edge_keyed_tiebreak). Fix: realize the group as ONE contracted
     vertex per this subsection -- its out-edges the union of members' producers,
     each in-edge keyed by the member the consumer actually reads. -->

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

**"A read of `f`" is any read in the *realized* loop nest, reached through the
site func's callees — not only reads written literally in the site func's own
definition.** There are two notions of "consumes" and this rule uses the loop-nest
one. To find where `f` is actually read, expand the site func's dependencies
through the funcs it calls: a *pure inline* callee is substituted in, so its
reads of `f` become reads inside the site func; a *realized* callee (one given a
`produce` block — e.g. a Func with an update definition, which cannot be inlined
and defaults to being computed at its consumer's innermost loop, §11) contributes
the reads of `f` inside *its* realization, which sits wherever that callee is
placed. Either way the level must enclose those reads. So `f.compute_at(site, v)`
is legal when `f` is read only by a callee that is itself realized inside the
site's nest — the callee's `produce` block (and the read of `f` within it) lies
inside the chosen `v` loop
([examples/compute_at_inline_dependence.hpp](examples/compute_at_inline_dependence.hpp):
`p.compute_at(out, y)` is legal in all three of the pure-inline, update-inline,
and `compute_root`-intermediate cases, because in each the intermediate reading
`p` is realized — or inlined — inside `out`'s `y` loop).

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

### `rfactor` rewrites a stage's LHS/RHS — a per-`(specialization, stage)` edit

Read the "rewrite" above as a concrete edit to the definition's **LHS/RHS state**
(§1). `rfactor` **returns a genuinely new Func** — the *intermediate*, a real
pipeline node, **not** a lazily-substituted wrapper like `in`/`clone_in` (§13).
It then rewrites the **`Stage` you called it on** (the *merge*): both that stage's
**left-hand-side index expressions** and its **right-hand-side value expressions**
change. This is a scheduling-directed edit of the algorithm's LHS/RHS that
nonetheless **preserves functional equivalence** (§1): the reduction is
re-associated, not changed.

It is specifically the **RHS** rewrite that makes the intermediate a *producer* of
the original Func — the merge's RHS now reads `intm(...)` (§1: a stage's producers
come from what its expressions read). The **LHS** rewrite instead sets the merge's
output index to the plain pure vars, and never reads the intermediate: a no-op for
an ordinary reduction (whose index already was the pure vars), but real for a
data-dependent scatter — a histogram `g(f(r.x, r.y)) += 1` has its scatter index
`f(...)` moved onto the *intermediate*, leaving the merge's LHS the plain `g(x)`
(so the read of the scattered-over input moves to the intermediate too).

The edit lands on **whichever definition the handle you called `rfactor` on
addresses**, and a handle is specific to a `(specialization, stage)` pair:

* `f.update(n)` addresses update stage *n*'s **base** definition;
* `f.update(n).specialize(cond)` addresses **that branch's own copy** of update
  stage *n*'s definition (§15 — each specialization forks the whole definition,
  LHS/RHS included).

So `rfactor` **composes with `specialize` orthogonally**: applied through a
specialization handle it rewrites **only that branch's** LHS/RHS, leaving the
other branches and the fallback with the definitions they already had. Different
branches then run **different (but functionally equivalent) reduction
algorithms** — the factored branch reads the intermediate (one reduction loop,
over the preserved `RVar`s), the others reduce as before — and, because the
intermediate is only referenced from the branch(es) that were factored, it is
**computed only on the path(s) that use it** (its production is guarded by the
branch condition). This is not a contradiction of "editing the algorithm": the
LHS/RHS is per-branch scheduling state (§1), edited independently per branch and
functional-equivalence-preserving each time.

The common form is `f.update(n).specialize(cond).rfactor(...)` — factor one
branch (e.g. a fast path) while the fallback stays the naive reduction. Other
shapes follow the same rule: `rfactor` **then** `specialize` the returned
intermediate ([examples/rfactor_specialize.cpp](examples/rfactor_specialize.cpp)
specializes the intermediate's partial-reduction stage), or nesting
`specialize → rfactor → specialize → rfactor` to give several branches their own
factored (or unfactored) reductions.

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
identity `f_in_g(args) = f(args)`; `g` reads `f_in_g` where it used to read `f`,
and `f`'s other consumers are untouched. The wrapper is pure, so at its default
it inlines straight back (the nest is just `f` → `g`); give it a compute level to
make it a distinct node (realization order `f` → `f_in_g` → `g`):

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

Forms: `f.in(g)`, `f.in({g1, g2, …})` (one shared wrapper for several named
consumers), and `f.in()` (a single **global** wrapper used by every consumer that
has no custom wrapper of its own). A global wrapper coexists with custom ones: a
consumer with its own custom wrapper uses that (**custom takes precedence**),
everyone else uses the global one — **except `f`'s own wrappers, which always
read `f`** ([in_custom_and_global.cpp](examples/in_custom_and_global.cpp): `g1`'s
custom wrapper and the global wrapper are siblings, both reading `f`). Common
uses: a per-consumer staging point for a shared producer, and repairing the "two
consumers force `f` to `root`" situation (§7,
[neg_compute_at_two_consumers.cpp](examples/neg_compute_at_two_consumers.cpp)) by
wrapping `f` separately per consumer.

### `f.clone_in(g)` — an independent *clone*

`f.clone_in(g)` returns a new Func that is a **copy of `f`'s entire definition**
(all stages, schedule, and specializations), and makes `g` read the clone. Unlike
a wrapper, the clone *recomputes* `f`'s work rather than reading `f`'s result, so
`f` and the clone are independent and may be scheduled differently.

Each wrapper/clone is a **distinct** Func with its own auto-generated name
(`f_in_g`, `f_clone_in_g`, plus an internal `$n` suffix the printer strips) — a
separate node in the nest (§10 normalizes names to positional ids), never "the
same Func twice."

### Two phases: eager at call time, deferred at lowering

Almost every surprise in this section comes from `in`/`clone_in` doing their work
in two phases:

- **Call time (eager)** — the moment you call `f.in(g)` / `f.clone_in(g)`:
  * the new Func is built. An `in` wrapper is a fresh identity `wrapper(args) =
    f(args)`. A **clone deep-copies `f`'s *current* contents** — definition,
    schedule, specializations — so it **freezes** whatever `f`'s body reads right
    now (its callees are *shared*, not copied — see the shared-inputs surprise).
  * the **recursive search** (next subsection) picks which Funcs to redirect and
    records `{pinned Func → new Func}` on `f`. **This pin set is frozen here and
    never recomputed.**
- **Lowering (deferred)** — when the nest is built:
  * each pinned Func's calls to `f` are rewritten to the new Func (the only point
    a consumer's body changes);
  * each pin is re-checked — the pinned Func must *still* call `f`, else the
    schedule is rejected.

The consumer Funcs are untouched at call time; only the record on `f` changes
(see the Implementation note). Two ordering facts follow, and both have sharp
exceptions covered in the surprises below:

- A deferred rewrite does not change the call **graph**, so it does not change the
  pin-*set* a later call's search computes. It *does* change `f`'s recorded
  **wrapper map**, and a later call **reuses/validates against that map** (keyed
  on its first pin, `get_wrapper` in `src/Func.cpp`). So wrap-vs-wrap order is
  free only when the two calls' resolved pin-sets are **equal or disjoint**;
  when they **overlap but are unequal**, order is observable — it can reject or
  silently under-wrap (colliding-pin-sets surprise).
- An **eager** rewrite of a definition (notably `rfactor`, §12) *does* change the
  call graph, so its order relative to a wrap changes the pin-set itself
  (stale-pin surprise).

### Which Funcs are pinned: the recursive search

The named consumers are not necessarily the Funcs that get redirected. For each
named consumer, Halide walks **down the current call graph** toward `f` and pins
the **first Func that directly calls `f`** on each branch:

```
pin_targets(f, consumer):
    descend `consumer`'s direct calls in the CURRENT graph:
        if this Func directly calls f  ->  pin it; stop descending this branch
        else                            ->  recurse into each direct callee
    if no Func on any branch calls f    ->  pin `consumer` itself
                                            (typically fails the lowering re-check)
global f.in()  ->  every direct caller of f, minus f's own wrappers and any
                   consumer that already has its own custom wrapper
```

Two properties of this walk drive the surprises below:

- It reads the graph **as written now** and is **blind to wrappers/clones** — the
  deferred rewrites are invisible to it, so it plans against *pre-rewrite* edges.
- A pin lands on a **shared** Func; rewriting that one body redirects it for
  **every** consumer of it, not only the consumer you named.

([src_doc: in/clone_in transitivity](src_doc/in_clone_in_transitivity.md) traces
the search and its lowering-time application in source.)

### Surprise: the named consumer is usually not the Func modified

The search pins the *first direct caller* of `f`, so naming `g` redirects
whatever sits on the frontier below `g`, not `g` itself — and because that Func
is shared, its *other* consumers get redirected too. In
[in_but_inlined.hpp](examples/in_but_inlined.hpp), `common.in(c1)` where `c1`
reads `common` only through `maybe_inlined` pins the wrapper on **`maybe_inlined`**;
a sibling `c3` that also reads `maybe_inlined` then reads the wrapper as well
(unrequested), while `c3`'s own *direct* reads of `common` stay on the original.
So "wrap for `g`" means "redirect the first direct callers of `f` beneath `g`,"
not "make `g` and everything under it use the wrapper." (A derived Func such as an
`rfactor` intermediate can be a pin target like any other; the full
partial-routing table is in the [src_doc](src_doc/in_clone_in_transitivity.md).)

### Surprise: pins freeze at call time — `rfactor` order matters (wrap order doesn't)

The lowering re-check rejects a pin whose Func no longer calls `f`:

> `Cannot wrap "f" in "g" because "g" does not call "f"`

Two ways to hit it:

- **No path to `f` at all** — the search falls back to pinning the named consumer
  itself, which never calls `f` (`out.clone_in(g)` when `g` reads `f` but not
  `out` — [clone_in_unused.cpp](examples/clone_in_unused.cpp), a negative example).
- **A later eager rewrite severs the pinned call.** The pin is frozen at call
  time; `rfactor` (§12) then rewrites the definition, moving the read of `f` into
  a new intermediate, so the pin goes *stale*:

```
rfactor(h) THEN clone_in({g,h}): search sees h → h_intm → f, pins h_intm   (legal; naming h_intm is redundant)
clone_in({g,h}) THEN rfactor(h): pins h (h→f then); rfactor makes h→h_intm  (stale pin → the error above)
```

`rfactor` is an eager rewrite of the graph the search reads, so its order versus
a wrap changes the pin-set. Fix: `rfactor` first, then wrap.
(`clone_specialize_matrix_impl.hpp` choiceB=2 is the stale negative, choiceB=3 the
working order — the stale pin is not a claim `h` can't reach `f`; it still does,
via `h_intm`, but the pin was taken on `h`.)

### Surprise: colliding pin-sets — wrap order *can* matter

Two `in`/`clone_in` calls whose resolved pin-sets **overlap but are unequal** are
order-dependent, even though both are lazy wraps and neither changes the call
graph. `get_wrapper` decides reuse from the call's **first** pin (`fs[0]`) and
`validate_wrapper` (`src/Func.cpp`) demands the reused wrapper's recorded consumer
set match *exactly*. With `a → common1`, `b → common1`, and `out1 → {common2, mid}`,
`out2 → {common2}` both funnelling to `common1` (pin-sets `{common2, mid}` and
`{common2}`, overlapping on `common2`):

```
in(out1) then in(out2): out1 registers {common2→w, mid→w}; out2 reuses via common2,
                        but mid shares w and is not in out2's set  -> CompileError
in(out2) then in(out1): out2 registers {common2→w}; out1 reuses via common2 and
                        SILENTLY drops the extra key mid           -> mid keeps reading the original
```

Which failure you get even depends on which pin sorts first alphabetically (it
becomes `fs[0]`): a different sort routes the same collision through
`get_wrapper`'s other reject path ("… already has a wrapper while … doesn't").
So *equal* pin-sets are order-free (the second call is an idempotent reuse — only
the wrapper's generated name follows the first call) and *disjoint* pin-sets are
order-free (two independent wrappers,
[probe/probe_in_two_wrappers_levels.cpp](probe/probe_in_two_wrappers_levels.cpp)
schedules them at different levels), but *overlapping-unequal* sets are not.
Two consumers funnelling into `f` through a shared intermediate therefore cannot
be given separate wrappers. (Source walk + both orders:
[probe/probe_in_key_set_collision.cpp](probe/probe_in_key_set_collision.cpp).)

### Surprise: the search is blind to pending rewrites — a clone can feed a consumer you didn't name

Because the search ignores earlier wraps' deferred rewrites, it can pin on a Func
the named consumer will not actually read in the final graph. With `f.clone_in(g)`
already recorded (final graph `g → f_clone_in_g`, `h → f`), calling
`common.clone_in(g)` still walks the *pre-wrap* `g → f → common` and pins on `f`:

```
walk from g:  g → f (pre-wrap) → common     ⇒ pin f
lowering:     f's read of common → common_clone_in_g
              f_clone_in_g is a frozen copy of f's body ⇒ still reads the original common
```

so the clone requested "for `g`" is read by **`h`** (the only post-wrap reader of
`f`), while `g` reads the original `common`: `common_clone_in_g.compute_at(h, y)`
is legal, `compute_at(g, y)` is not
([indirectly_reached_clone.hpp](examples/indirectly_reached_clone.hpp);
order-independent, and `common.clone_in(h)` gives the same result since both `g`
and `h` route through `f`).

### Surprise: a clone shares `f`'s inputs (callees are not copied)

The deep copy duplicates `f` but reads the **same** producers `f` reads. So if
`f` reads `p`, then after `f.clone_in(g)` the Func `p` is read in two places (`f`
and the clone) and the only level enclosing both is `root`: `p.compute_at(f, x)`
becomes **illegal** — Halide lists `p` "used in" both, with only
`p.compute_root()` legal
([neg_clone_shared_callee.cpp](examples/neg_clone_shared_callee.cpp)). To give a
clone private inputs, clone those too. (`Func::clone_in`'s "intermediate Funcs
along the path" is the *caller* chain between the consumers and `f`, not `f`'s
callees.)

### Surprise: a clone can delete `f`; a wrapper never does

A redirected caller reads the new Func instead of `f`, so `f` keeps only its
non-redirected readers. A wrapper reads `f`, so **`f` always survives an `in`**.
A clone reads `f`'s *inputs*, so if **every** reader of `f` is redirected to the
clone, `f` becomes unreachable (§1) and **drops out of the nest**. For the chain
`h → g → f` with no other reader: `f.in(h)` prints `f → f_in_h → g → h` (`f`
stays); `f.clone_in(h)` prints `f_clone_in_h → g → h` with `f` absent
([in_transitive.cpp](examples/in_transitive.cpp) vs
[clone_transitive.cpp](examples/clone_transitive.cpp)).

### Limitation: a Func can be cloned only once

`clone_in` deep-copies `f`'s schedule but not the wrapper entries that schedule
now holds, so a **second, distinct** clone/wrap on an already-wrapped Func aborts
(`copied_func.defined()` in `FuncSchedule::deep_copy`). `f.clone_in(a)` then
`f.clone_in(a)` is fine (returns the first clone); `f.clone_in(a)` then
`f.clone_in(b)`, or `f.in(a)` then `f.clone_in(b)`, crashes. `in()` is exempt (it
never deep-copies). Known, still-open upstream bug
([#6476](https://github.com/halide/Halide/issues/6476),
[#3661](https://github.com/halide/Halide/issues/3661)), undocumented in the API.

### Interaction with `specialize`

Wrappers and clones are keyed by **consumer Func**, with no notion of a
specialization branch. A single `f.in(g)` / `f.clone_in(g)` wrapper is read by `g`
in **all** of `g`'s specialization branches (§15) — there is no per-branch
wrapper. Correspondingly the consumer argument must be a **`Func`**: a
`g.specialize(cond)` handle is a `Stage`, not a `Func`, so it **cannot be passed**
to `in`/`clone_in` at all (it does not compile) — you cannot "wrap only one
branch." This is the same one-schedule-per-Func fact behind §15's note that a
producer cannot be scheduled per consumer branch. (If instead the *wrapped* Func
`f` is the one specialized, nothing special happens here: consumers read `f`, and
`f`'s branches live inside its own `produce`, per §15.)

The two directives differ in what they carry over from a specialized wrapped Func.
A **clone** is a *deep copy* of the wrapped Func's whole state — definition,
schedule, **and its specializations** — so the clone starts with an independent
copy of those branches
([examples/specialize_clone_inherits.cpp](examples/specialize_clone_inherits.cpp):
`f` is specialized, and `f.clone_in(g)` prints with the same two branches). An
**`in` wrapper**, by contrast, is a *fresh* pointwise Func (`wrapper(args) =
f(args)`) with its own empty schedule — it does **not** inherit `f`'s
specializations.

### Misc examples

(Examples for the specific surprises above are cited inline in each subsection.)

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
(§1) on the child — and the nest is built from it later (§16). Because the state
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

For **realization order** the whole group is **one contracted vertex** (§6 "Fused
groups: one contracted vertex"): the members' out-edges (to their producers) and
in-edges (from consumers) are all preserved with their labels, so the group is
placed once — after everything any member reads, before any consumer of any
member — while an in-edge still carries the label of the specific member a
consumer read (the group has no label of its own). The rest of this section is the
group's *internal* structure — member order and how the stages interleave.

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

## 15. `specialize`: conditional schedule variants

`f.specialize(cond)` gives a definition a **conditional variant**: at run time, if
`cond` holds, a specialized schedule is used; otherwise a fallback runs. In the
printed nest this shows up as **several loop nests where you'd expect one** —
Halide emits the branch nests back to back.

### The state it records

`specialize` is per **definition** — the pure definition, or a specific update
stage (`f.update(n).specialize(...)`). Each definition carries an **ordered list
of specializations**; each specialization pairs a condition with **its own copy
of the schedule** (a forked Definition). This per-stage affinity is only visible
on an impure Func (one with update stages): specializing one stage expands only
*that* stage's nest, leaving the others as single nests inside the same
`produce f` ([examples/specialize_update_stage.cpp](examples/specialize_update_stage.cpp)
specializes only the update stage; [examples/specialize_both_stages.cpp](examples/specialize_both_stages.cpp)
specializes the pure and update stages independently, giving four nests — two per
stage — under one `produce f`). `f.specialize(cond)`:

* appends a specialization whose schedule is a **copy of the schedule so far** —
  every directive issued on `f` *before* this `specialize()` call
  ([examples/specialize_inherit.cpp](examples/specialize_inherit.cpp): a `tile`
  before `specialize` is inherited by the branch, which then adds a `split`); and
* returns a **handle** to that copy, so further scheduling on the handle affects
  the branch only.

The forked copy is the **whole definition** — its LHS/RHS index and value
expressions as well as its schedule (§1). So a directive that edits the LHS/RHS,
applied through a branch handle, edits **only that branch**:
`f.update(n).specialize(cond).rfactor(...)` factors that branch's reduction alone
(functional-equivalence-preserving, §1), leaving the other branches and the
fallback with their original definitions (§12 "`rfactor` rewrites a stage's
LHS/RHS").

Directives issued on `f` **after** a `specialize()` call modify the parent
(fallback) schedule, not the already-forked specialization
([examples/specialize_fallback_scope.cpp](examples/specialize_fallback_scope.cpp):
a `split` added after `specialize` lands on the fallback only). A specialization
may itself be specialized, nesting the variants
([examples/specialize_nested.cpp](examples/specialize_nested.cpp)).
`f.specialize_fail(msg)` appends a terminal specialization that aborts at run
time instead of providing a fallback; nothing may be specialized after it.

### How it becomes loops

A definition's specialization list lowers to a chain of `if`/`else`: the
specializations become the `if`/`else if` arms, in **declaration order**, with
the unspecialized default as the final `else` — `if cond_0 { branch_0 } else if
cond_1 { branch_1 } … else { fallback }`. "Declaration order" is simply the order
the `specialize()` calls were made in your C++ program (program order); the
conditions are tested first-declared-first, so the first matching arm wins.  Each
branch is a full loop nest built from **that branch's** copied schedule (§16
applied to the fork).

Because a specialization's forked copy is itself a full definition with its own
(initially empty) specialization list (§1), specializations form a **tree**, and
*which handle* you call `specialize` on decides the shape:

* Calling `specialize` again on the **same** `Func`/`Stage` handle appends another
  arm to *that* definition's list — the arms are **siblings**, giving one **flat**
  `if / else if / … / else` chain.
* Calling `specialize` on the **handle returned by** an earlier `specialize`
  descends into that branch and appends to *its* list — a **child**, giving a
  **nested** `if` inside that branch's arm
  ([examples/specialize_nested.cpp](examples/specialize_nested.cpp) nests one
  branch inside another).

Mixing the two builds an arbitrary tree.
[examples/specialize_tree.cpp](examples/specialize_tree.cpp) does both: with
`fa = f.specialize(cond_a)`, then `f.specialize(cond_b)` (a **sibling** of
`cond_a`, added to `f`), then `fa.specialize(cond_c)` (a **child** of `cond_a`,
added to `fa`), the result is

```
if cond_a:
  if cond_c: … else: …        // cond_a && cond_c ; cond_a && !cond_c
else:
  if cond_b: … else: …        // !cond_a && cond_b ; !cond_a && !cond_b
```

so `cond_c` is only tested when `cond_a` holds, and `cond_b` only when it does
not — four leaf cases. (The `specialize` handle is a `Stage`, so store it as
`Stage fa = f.specialize(cond_a);` — see §13 for why it is not a `Func`.)

`print_loop_nest()` does not print conditions or any `if`/`else` marker: it walks
into **every** branch and prints each branch's loop nest, so the branches appear
**concatenated as sibling subtrees under the same `produce`** node, in that same
order — specialization branches first (declaration order), fallback last
([examples/specialize_basic.cpp](examples/specialize_basic.cpp): a tiled branch,
4 loops, then the plain fallback, 2 loops, both inside one `produce f`). A
specialized **producer** is no different — its branches sit inside its own
`produce` block ([examples/specialize_producer_self.cpp](examples/specialize_producer_self.cpp)).

Two consequences of Halide simplifying the nest before printing:

* **`specialize_fail` prints no fallback.** Its else-branch is an assertion, which
  carries no loops, so only the specialization branches appear
  ([examples/specialize_fail.cpp](examples/specialize_fail.cpp)).
* **Identical branches merge.** If a branch's nest is *identical* to the fallback
  it wraps (same loops, same order, same nested producers — i.e. the
  specialization changed nothing structural), Halide folds the if/else back into
  one copy, so that specialization leaves no trace. A branch that differs only in
  a way this document's structural comparison ignores (the order of two plain
  serial loops, a constant tile size) is still a *distinct* nest and is printed
  separately. In practice every useful specialization changes the structure, so
  each one prints its own subtree; the examples here are all structurally
  distinct per branch.

### Producers under a specialized consumer

A producer computed (or stored/hoisted) at a loop of a specialized consumer is
injected **separately into each branch**, resolved against **that branch's** own
loop nest — each branch has its own copy of the dimension list (§9), so the
producer follows the branch's structure
([examples/specialize_producer_at.cpp](examples/specialize_producer_at.cpp): the
specialization splits an outer loop, and the `compute_at` producer is injected at
the inner loop of *each* branch's nest). A producer computed **outside** the
consumer (e.g. at `root`) is emitted once, before the consumer, and is not
duplicated per branch
([examples/specialize_producer_root.cpp](examples/specialize_producer_root.cpp)).
The specialized Func need not be the output or its direct producer: it can sit
deep in the pipeline, with a producer below it and a consumer above
([examples/specialize_midchain.cpp](examples/specialize_midchain.cpp): the middle
Func of an `a → b → c → out` chain is specialized).

`specialize` forks **only the specialized Func's own schedule**. It does **not**
reach into its callees: a producer is a separate Func with **one** definition and
**one** schedule, shared by every branch. So the only per-branch variation a
producer can show *through scheduling* is its **placement** — which happens when
the `compute_at` level names a loop that the consumer's branch has moved (by a
per-branch `reorder`/`split`/`rename`). It is **impossible, through scheduling**,
to compute a producer *differently internally* (its own splits, its own compute
level) depending on which branch of a consumer uses it — `in`/`clone_in` do not
provide a way either: they redirect a consumer's reads to a **single** wrapper/
clone Func with one schedule, read in all of the consumer's branches (§13).
*Unless* you step outside scheduling entirely and change the algorithm, in the
narrow and fragile way described next.

### Known limitation: no per-branch producer scheduling

A recurring wish is to compute a producer one way when a consumer took its `cond`
branch and another way otherwise. There is **no scheduling directive for this**,
and it is worth stating plainly rather than implying a clean idiom exists.

The only thing that achieves it is an **algorithm** change: give the consumer two
distinct producers and pick between them with `select` —
`f(x,y) = select(cond, g(x,y), gc(x,y))` together with `f.specialize(cond)`. The
specializer simplifies the branch's *value expression* using the known condition,
collapsing the `select` to `g(x,y)` in the `cond` branch (and `gc(x,y)` in the
fallback); as a downstream consequence each branch then references — and so
schedules — only its own producer. This is **not a scheduling technique** and
should be treated as a last resort:

* It edits the algorithm (what `f` computes), so it forfeits Halide's core
  guarantee that scheduling cannot change results. Nothing checks that `g` and
  `gc` are equivalent — if you intend them to be, keeping them in sync is on you,
  unverified.
* It works only as a **side effect** of a value-simplification pass
  (`simplify_specializations`), and only when the specialization condition is a
  bare boolean parameter or `var == const`; for other conditions the `select` is
  not guaranteed to be pruned, in which case *both* producers are scheduled and
  the intended effect **silently** does not happen. See
  [src_doc: specialize](src_doc/specialize.md) for the mechanism and
  `probe/SPECIALIZE_FINDINGS.md` / `probe/probe_specialize_case2.cpp` for worked
  cases; whether Halide intends this to work at Func granularity is an open
  question, not settled behavior.

This document does not treat `select`-pruning as part of the scheduling model, and
**simplifying `select` (and thus this per-branch-producer behavior) is out of
scope for `micro_halide`**: `micro_halide` does not analyze `Expr`s, so an example
relying on it cannot be reproduced structurally and none is provided.

### Legality

The Func that **calls** `compute_with` must have no specializations: a fused group
is emitted as one shared, unconditional loop nest (§14), which has no room for a
member's per-branch variants. `f.specialize(cond); f.compute_with(g, v)` is
rejected ([examples/neg_compute_with_specialize.cpp](examples/neg_compute_with_specialize.cpp)).
The restriction is on the caller (the member being fused in), not on the target
`g` (see [src_doc: compute_with/legality](src_doc/compute_with/legality.md)).

### Out of scope

* **Condition de-duplication.** Re-calling `specialize` with an *equal* condition
  `Expr` returns the **handle to the existing** specialization rather than
  appending a new one. This document does not model that: the examples always use
  **distinct** conditions (e.g. separate `Param<bool>`s), so each `specialize`
  call is a new branch and no `Expr`-equality bookkeeping is needed.
* **Identical-branch merge.** The "How it becomes loops" note above — that Halide
  folds an if/else whose branches are structurally identical into one printed copy
  — is a `simplify()` effect that `micro_halide` need **not** reproduce: it may
  emit one loop nest per branch (specializations then fallback) without merging.
  Every example here has structurally distinct branches, so no merge is exercised;
  matching Halide's merge would require true-IR-identity comparison, which is out
  of scope.
* **Per-branch loop elision.** Placing a producer at a *different loop* per branch
  (e.g. `compute_at(f, y)` in one branch, `compute_at(f, x)` in another) gives it
  a different required region — and so a different set of elided (point) loops —
  per branch. The declared-elision annotation (`micro_halide_collapses`, §7) is
  keyed per producer-stage, not per branch, so it cannot express that asymmetry;
  such an example is deferred, exactly as with the multi-host-stage elision case.
  The examples here keep each producer's elision the same across branches.

See [src_doc: specialize](src_doc/specialize.md) for the compiler-level account
(the `Specialization` list, the `IfThenElse` lowering in `build_provide_loop_nest`,
and why the printer shows branches with no condition).

## 16. Putting the algorithm together (how the nest is built)

The whole loop nest follows from the rules above, assembled into one procedure:

1. **Force the output to `root`** — the Func you call `print_loop_nest()` on is
   always computed at the outermost level (§5, §6).

2. **Compute the realization order** — order the pipeline so every producer
   precedes its consumers, by the exact procedure in §6 ("Realization order in
   detail" — a post-order DFS from the output, not a global sort). All Funcs remain
   in this order so they can pass dependencies along; a **pure inline** Func
   (§4) is never realized and drops out of the steps below (§5), but a non-pure
   inline Func *is* realized (§11) and keeps its slot. An `rfactor` intermediate
   (§12), and any `in`/`clone_in` wrapper or clone (§13), are likewise ordinary
   Funcs in this order — a wrapper/clone sits between the wrapped Func and the
   consumers it was created for — with whatever schedule each was given. A
   **fused group** (§14) is a single contracted vertex here (§6 "Fused groups: one
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
[legality](src_doc/compute_with/legality.md)), and
[specialize](src_doc/specialize.md).
