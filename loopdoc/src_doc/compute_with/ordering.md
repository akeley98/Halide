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

## OPEN DOC GAP — the precise tie-break is NOT §6 name order

loopdoc §14 currently states the member/stage ordering tie-break as the §6
name order. That is **wrong in two topologies**, confirmed by failing copied
tests:

* **Chains.** `g.compute_with(f)`, `h.compute_with(g)` — the realization order
  follows the *chain* (the topological order over fuse edges), not §6 name order
  among the non-direct-children. §14's "§6 tie-break with spine owner last" gives
  the wrong produce nesting here. (`cwtest_mixed_tile_factor`.)
* **Free/unfused stages within a group.** Their relative order follows the
  within-group realization order (i.e. the `temp` topological position above —
  spine owner last), not §6 name order. (`cwtest_overlapping_updates`,
  `cwtest_update_stage_diagonal`, `cwtest_child_var_dependent_bounds`.)

So the real rule is: **the within-group order is the topological order over the
fuse-edge structure** (children before parents ⇒ spine owner last; chains
deepest-first), with the §6 tie-break breaking ties only among members/stages
that the fuse edges leave unordered. The precise, fully-traced statement is still
to be written (this is the active doc-gap; see `progress.txt` DISCOVERED DOC
GAPS), at which point loopdoc §14's member-ordering subsection and this file
should be tightened together. `compute_with_chain.cpp` passes only because its
names happen to make §6 order coincide with the chain order — it does not
discriminate the rule.
