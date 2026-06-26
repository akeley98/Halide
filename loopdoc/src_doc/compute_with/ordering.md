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
"exploiting that stages of a function form a linear order". The construction
(~1761–1800) is literally a **repeated sweep**, and the body/compute order **is**
this sequence:

    while (complete_count < funcs.size()) {           // sweep until all placed
      for i in 0..funcs.size():                        // members in funcs order
        while next stage of funcs[i] has 0 deps:       // emit its ready stages,
          stage_order.push(funcs[i], stage_index[i]);  //   bundling consecutive
          decrement deps of its dependents; stage_index[i]++;
        // first blocked stage of funcs[i] -> break, move to next member
      if (!progress_made) error "cycle inside of the fused group";
    }

A stage's dep count (~1721–1753) is +1 if it has a fuse level (its parent stage
must precede it), plus back-edges so that a parent stage `g.q` carrying a fused
child `f.s(p)` waits for all of `f`'s earlier stages `s0..s(p-1)`
(~1744–1749; skipped for the consecutive-same-parent special case). Each Func's
own stage order is structural — the inner `while` only advances `stage_index[i]`
in sequence. So a member whose next stage is **blocked** is skipped this sweep
and **revisited** later; its blocked stage can land after stages of members that
come later in `funcs`. Unfused stages start their own sibling nest; fused stages
splice into their parent (`inject_stmt`, ~1843) and never appear at top level.

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

### Worked sweep: `cwtest_update_stage_diagonal`

The body order is the sweep above, not "each free stage at its member's slot" —
the discriminating case is a free stage stuck behind a fused sibling.
`f.update(1).compute_with(g.update(0))` and `g.update(0).compute_with(h)` give
`f.s2`→`g.s1`, `g.s1`→`h.s0`; `f.s1`, `g.s2` unfused; `funcs = [f, g, h]`.

* sweep 1 — `f`: emit `s0`, `s1`; `s2` blocked on `g.s1`. `g`: emit `s0`; `s1`
  blocked on `h.s0` ⇒ `g` stalls (`s2` stuck behind). `h`: emit `s0` (frees
  `g.s1`'s dep), `s1`, `s2`.
* sweep 2 — `g`: `s1` ready, emit (splices into `h.s0`), then free `s2`; `f`:
  `s2` ready, emit (splices into `g.s1`).

Top-level body (unfused stages only): `f.s0, f.s1, g.s0, h.s0`(+spliced `g.s1`,
`f.s2`)`, h.s1, h.s2, g.s2` — the free `g.s2` lands **last**, after all of `h`,
because `g` stalled in sweep 1 and was revisited only after `h.s0` freed `g.s1`.
A single pass that "emits each free stage at its member's slot" would instead put
`g.s2` before `h` — which is the trap that earlier wording set. Backs loopdoc §14
"The two observable orders".
