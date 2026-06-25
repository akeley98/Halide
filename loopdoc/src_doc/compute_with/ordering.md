# compute_with: member and stage ordering within a group

_Part of the [src_doc set](../README.md); backs loopdoc §14 (compute_with). The
compute_with topic is split into [fused_groups](fused_groups.md),
[growth](growth.md), [member_sites](member_sites.md), [ordering](ordering.md),
[legality](legality.md)._

Two orders matter: the **within-group realization order** of the members (which
drives produce/consume nesting), and the **stage order** of the interleaved body
(which drives compute/body order). Both come from two places.

## Within-group realization order (`RealizationOrder.cpp`)

After the group is collapsed to a dummy node (see [fused_groups](fused_groups.md)),
sibling order in the DAG is decided by `sort_funcs_by_name_and_counter` (~256) —
the (prefix, visitation counter, full name) tie-break, same as loopdoc §6. The
members **within** a group are then ordered by their position in the topological
result `temp` (~403), and `funcs.back()` is treated as the `group_parent` /
spine owner by the emitter. Produce/consume nesting is the **reverse** of this
order (`funcs.back()` outermost — see [growth](growth.md)).

## Stage order of the body (`build_pipeline_group`)

`stage_order` (~1755) is a topological sort over the stages' `fuse_level` edges,
"exploiting that stages of a function form a linear order": each Func's own
stages in order (`s0` before `s1` …), and each parent stage before the child
stage fused onto it. Unfused (free) stages and the fused parent stages are
placed by this same sort.

## The precise rule (confirmed) — NOT §6 name order

The within-group order is **the topological order over the fuse-edge structure:
each child before its parent ⇒ the spine owner (root) last**, with the §6
tie-break breaking ties **only** among members/stages the fuse edges leave
unordered (e.g. several direct children of one parent). It is *not* a §6-name
order with the owner moved last; that only coincides when names happen to match
the fuse order. (loopdoc §14's member-ordering subsection was corrected to match;
this resolved the doc-gap. micro_halide still implements the old §6-name version
— an overfit awaiting a hardening micro-agent.)

Evidence (`[loopdoc-trace]` against real Halide):

* **Chain** `g.compute_with(f)`, `h.compute_with(g)` → `funcs = [h, g, f]`
  (deepest-child-first), produce nesting `f, g, h`; micro gives `f, h, g` (§6
  name). `cwtest_mixed_tile_factor`. (`compute_with_chain.cpp` *passes* only
  because its names make §6 order coincide with the chain order — it does not
  discriminate the rule.)
* **Free/unfused stages** — `g.update().compute_with(f.update())` with `f`,`g`
  each having an unfused pure stage → `funcs = [g, f]`; the body emits the free
  pure stages `g.s0, f.s0` (realization order, child-before-parent), *not* `f, g`
  (§6 name). `stage_order` from the trace: `g.s0` (own), `f.s0` (own), `f.s1`
  (own), `g.s1` (fused into `f.s1`). `cwtest_overlapping_updates`,
  `cwtest_update_stage_diagonal`, `cwtest_child_var_dependent_bounds`.

Body/compute order is the greedy emission: walk members in realization (`funcs`)
order emitting each member's *ready* stages — a free stage emits at its member's
slot; a fused child stage defers until its parent stage is emitted, then is
spliced in (`build_pipeline_group`'s `stage_order` loop, ~1755).
