# `compute_with`: fusing stages into a shared loop nest

Detail companion to the main [loopdoc.md](../loopdoc.md); section references "§N" point to that document.

How the members of a fused group interleave into one shared loop nest — member order, the two observable orders, loop ownership, and legality.

---

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
  one group ([examples/compute_with_chain.cpp](../examples/compute_with_chain.cpp));
* a Func's different stages can fuse into different parents — e.g.
  `f.compute_with(g)` with `f.update().compute_with(h)` puts `f, g, h` in one
  group while `f` has two parents
  ([examples/compute_with_two_parents.cpp](../examples/compute_with_two_parents.cpp)).

For **realization order** the whole group is **one contracted vertex**
([realization_order.md](realization_order.md) "Fused groups: one contracted
vertex"): the members' out-edges (to their producers) and
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
([src_doc: compute_with/growth](../src_doc/compute_with/growth.md); the
`[loopdoc-trace]` lines in any fused example's `debug_1` log print the member
order and the stage order directly).

Two granularities are in play, and keeping them apart is the whole trick of this
section: a fuse edge joins two *stages* (`f.s0` into `g.s1`), but the members are
ordered as whole *Funcs*. Read "Func A is a child of Func B" to mean *some* stage
of A fuses into a stage of B.

1. **Order the members (Funcs).** Topologically sort the Funcs with each child
   before its parent, breaking whatever the fuse edges leave unordered — several
   children of one parent, or one child's two parents — by the §6 realization
   order (the same name-then-visitation tie-break,
   [realization_order.md](realization_order.md)). Members have no
   producer/consumer dependency among themselves (a precondition) and no cyclic
   fuse edges (Legality), so the fuse edges are the only ordering constraint and
   the sort is well-defined. A chain `g.compute_with(f)`, `h.compute_with(g)` is
   deepest-child-first `h, g, f`. The last Func in this order — whatever the
   group's shape — is the **spine owner**. When one member is an ancestor of all
   the rest (children fused into one parent, or a chain) that ancestor is the
   spine owner; when none is — e.g. one child with two parents — it is simply
   whichever Func the tie-break places last (`h` in
   [examples/compute_with_two_parents.cpp](../examples/compute_with_two_parents.cpp),
   members `f, g, h`). It has two roles below: the outermost `produce` (step 3),
   and the sole owner of the real shared loops (Loop ownership). This is the
   group's within-group realization order
   ([src_doc: compute_with/ordering](../src_doc/compute_with/ordering.md)).
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
([examples/compute_with_split.cpp](../examples/compute_with_split.cpp)):

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
[examples/compute_with_update.cpp](../examples/compute_with_update.cpp) fuses both
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
([examples/compute_with_three.cpp](../examples/compute_with_three.cpp)). The
reverse-nesting holds even when the parent's name would sort it first
([examples/compute_with_parent_alpha.cpp](../examples/compute_with_parent_alpha.cpp)),
and a chain's member order is deepest-child-first, not §6 name
([examples/cwtest_mixed_tile_factor.cpp](../examples/cwtest_mixed_tile_factor.cpp)).

The sweep in step 2 is what makes the body order more than "each member's stages
at its slot": it matters whenever a member's next stage is blocked. In
[examples/cwtest_update_stage_diagonal.cpp](../examples/cwtest_update_stage_diagonal.cpp)
the members order `f, g, h` (`f.s2` fuses into `g.s1`, `g.s1` into `h.s0`) and
`f.s1`, `g.s2` are unfused. Sweeping `f, g, h`:

* `f` emits `s0`, `s1`; `s2` blocks (its parent `g.s1` isn't placed yet).
* `g` emits `s0`; `s1` blocks (parent `h.s0` not placed), stalling `g` — `s2` is
  stuck behind it.
* `h` emits `s0`, `s1`, `s2` (all unfused); placing `h.s0` unblocks `g.s1`.
* second sweep — `g` emits `s1` (now ready; spliced into `h.s0`) then the free
  `s2`; `f` emits `s2` (spliced into `g.s1`).

Only the unfused stages start their own sibling nests, so the top-level body runs
`f.s0, f.s1, g.s0, h.s0, h.s1, h.s2, g.s2` (with `g.s1` spliced into `h.s0`, and
`f.s2` into `g.s1`). The
free `g.s2` lands last, after all of `h` — not "at `g`'s slot" — because `g`
stalled in the first sweep behind its fused `s1` and was not revisited until
`h.s0` freed it ([src_doc: compute_with/ordering](../src_doc/compute_with/ordering.md);
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

[examples/human_compute_at_compute_with_child.cpp](../examples/human_compute_at_compute_with_child.cpp)
makes this concrete: `parent`/`child` fuse at `y` (loops `z` outer, `y`, `x`), and
`g.compute_at(child, z)` realizes `g` inside `fused.y` at `child`'s slot (per-`y`),
whereas
[examples/human_compute_at_compute_with_child_no.cpp](../examples/human_compute_at_compute_with_child_no.cpp)'s
`g.compute_at(parent, z)` realizes `g` under the real `fused.z` loop, above
`fused.y` (per-`z`). Same named loop `z`, different sites. (Why, with source +
trace: [src_doc: compute_with/member_sites](../src_doc/compute_with/member_sites.md).)
Two consequences:

* A producer computed at `(member, v)` lands at that member's slot, and its
  legality is the ordinary §7 rule — the site must enclose every use of the
  producer; there is no special "name the parent" requirement. So naming a child
  is legal when the producer is used only within that child
  ([examples/compute_with_producer.cpp](../examples/compute_with_producer.cpp)
  computes a shared producer at the parent; a child site is equally fine when its
  body encloses every use), and illegal when a use lies outside it — e.g. when the
  producer is read by another member, as in
  [examples/neg_compute_with_producer_at_child.cpp](../examples/neg_compute_with_producer_at_child.cpp)
  (its `input` is read by both `f` and `g`, so the child's site doesn't enclose
  `f`'s use). This holds identically for `compute_at`, `store_at`, and
  `hoist_storage`. (A fused loop's bounds are the union over the members, so a
  producer there can keep a loop it would collapse at a plain `compute_at`; as
  always such elision is declared, not derived.)
* Which shared loop collapses to a point is (as always) a bounds question, not
  derived here. Only the **spine owner's** shared loops can print as real `for`s;
  any other member's shared loops are already extent-1 scheduling points that
  print nothing, and a below-`v` loop belongs to the member that owns it (which
  may differ between members).
  [compute_with_at.cpp](../examples/compute_with_at.cpp) collapses the shared `y`
  via the parent.

The same collapse drives a chain subtlety: a child's fuse level into its parent
should be **at or below the parent's own fuse level**. If a child fuses into a
non-spine-owner parent at a loop *above* that parent's fuse level, the loop is one
of the parent's collapsed dummies, so the child splices at the parent's slot and
the child's loops below `v` re-materialize as real loops nested inside the spine
owner's shared nest — recomputing the child redundantly (correct, just wasteful).
[examples/compute_with_chain_outer.cpp](../examples/compute_with_chain_outer.cpp) is
this surprise (`f` fused outer than `g`);
[_inner](../examples/compute_with_chain_inner.cpp) and
[_equal](../examples/compute_with_chain_equal.cpp) are the well-behaved cases.

### Legality

`compute_with` has its own preconditions, separate from the general
compute_at rule:

* The two fused stages must have matching loop nests down to `v` — checked on the
  resulting dimension lists (§3), not the scheduling provenance: `v` must exist by
  name in both
  ([examples/neg_compute_with_mismatch.cpp](../examples/neg_compute_with_mismatch.cpp)),
  and the count of loops from the outermost down to `v` must match (so `v` is at
  the same depth), paired up by name
  ([examples/neg_compute_with_dim_count.cpp](../examples/neg_compute_with_dim_count.cpp)).
  The paired-up shared loops must also agree in kind — a `Var` fuses with a `Var`,
  an `RVar` with an `RVar` (the fuse level itself may be either: fusing two update
  stages at a shared reduction var is fine,
  [examples/compute_with_rvar.cpp](../examples/compute_with_rvar.cpp)). In practice
  the kind follows from the name, since two stages rarely give the same name to a
  `Var` in one and an `RVar` in the other. The paired loops must also share the
  same **loop type** and device (§17): fusing a `parallel` dim with a `vectorized`
  one is rejected
  ([examples/neg_compute_with_fortype_mismatch.cpp](../examples/neg_compute_with_fortype_mismatch.cpp)),
  and the surviving shared loop carries that one type. Loops below `v` and all extents may
  differ — different `split` factors or matching `tile`s fuse fine
  ([examples/compute_with_tile.cpp](../examples/compute_with_tile.cpp)), but a
  `reorder` that moves `v`'s depth does not.
* The fused Funcs must have no producer/consumer dependency
  ([examples/neg_compute_with_dependency.cpp](../examples/neg_compute_with_dependency.cpp)).
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
  ([examples/neg_compute_with_level_mismatch.cpp](../examples/neg_compute_with_level_mismatch.cpp));
  [examples/compute_with_two_parents_at.cpp](../examples/compute_with_two_parents_at.cpp)
  is the legal cross-parent case at a shared `compute_at(out, y)`. The rule is
  exactly compute-level equality — members may still have different store levels.
* The stage order (above) must exist — `compute_with` may not pin a Func's stages
  so that no consistent order can. Since a Func's own stages are forced into order
  (`s0` before `s1` …), as a Func's stages advance the parent-stage index they
  fuse into must be **non-decreasing, and may repeat only across consecutive fused
  stages** (no unfused stage in between). Two failure shapes: a *decrease* — fuse
  `f.s0` into `g.s1` but `f.s1` into `g.s0`
  ([examples/neg_cwtest_crossing_edges2.cpp](../examples/neg_cwtest_crossing_edges2.cpp))
  — and a *repeat across a gap* — fuse `f.s0` and `f.s2` both into `g.s0` while
  `f.s1` is unfused
  ([examples/neg_cwtest_crossing_edges1.cpp](../examples/neg_cwtest_crossing_edges1.cpp)):
  the unfused `f.s1` must sit strictly between `f.s0` and `f.s2`, yet both are
  pinned to the single stage `g.s0`, leaving nowhere to put it. Either way Halide
  rejects up front with "impossible to establish correct stage order" — checked
  per Func, per parent ([src_doc: compute_with/legality](../src_doc/compute_with/legality.md)).
* The Func being fused in must itself have **no specializations** — a fused group
  is one unconditional shared nest with no room for per-branch variants; the rule
  and its example live in §15 Legality
  ([examples/neg_compute_with_specialize.cpp](../examples/neg_compute_with_specialize.cpp)).

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
bounds detail — they do not appear in the printed nest and are not modeled here.

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

