# compute_with: recorded state and fused-group formation

_Part of the [src_doc set](../README.md); backs loopdoc §14 (compute_with). The
compute_with topic is split into [fused_groups](fused_groups.md),
[growth](growth.md), [member_sites](member_sites.md), [ordering](ordering.md),
[legality](legality.md)._

`compute_with` creates no Function. It records a *fusion intent* on the **child**
stage's schedule; the realization-order and schedule-injection passes turn that
into the shared nest. The machinery lives in three cooperating places:
`RealizationOrder.cpp` (grouping), `Schedule.cpp`/`Func.cpp` (the per-stage
records), and `ScheduleFunctions.cpp` (the loop emission — see
[growth](growth.md)).

## API entry points and recorded state

`Stage::compute_with(LoopLevel, align)` (and the `Func` forwarders) is in
`src/Func.cpp` ~2098. It writes a single field — the child stage's
`StageSchedule::fuse_level()`, a `FuseLoopLevel{level, align}` (`src/Schedule.h`
~311). `level` is a `LoopLevel(parent_func, parent_stage, var)` naming where to
fuse; `align` is the bounds-only alignment map. The default `fuse_level` is
`inlined()`, meaning "not fused". So a `compute_with` is just "this child stage
carries a `fuse_level` pointing at a parent stage + var" — and re-calling it
overwrites that one field (loopdoc §14's "records state; a second call
overwrites").

`FusedPair{func_1, stage_1, func_2, stage_2, var_name}` (`src/Schedule.h` ~536)
is the materialized edge: *(parent stage) is fused with (child stage) at
`var_name`, parent computed first.* `populate_fused_pairs_list`
(`src/RealizationOrder.cpp` ~143) reads each definition's `fuse_level` and pushes
a `FusedPair` onto the **parent** stage's `fused_pairs()` list (init stage if
`stage_index == 0`, else the matching update).

## Grouping: connected components (`RealizationOrder.cpp`)

`realization_order` (~295):

* builds an **undirected** `fuse_adjacency_list` (~127) connecting every
  `func_1`↔`func_2`, then `find_fused_groups` (~38) runs a DFS so each
  **connected component** of Funcs becomes one **fused group** with a synthetic
  name. (This is why a chain `g.compute_with(f)`, `h.compute_with(g)` and a
  Func whose stages fuse into different parents both form a single group —
  loopdoc §14 "a per-stage relation".)
* The pipeline DAG is then computed with each group collapsed to a **single
  dummy node** (~360): every member depends on the dummy, and the dummy depends
  on all funcs the members call. So the whole group realizes **as a unit**, after
  everything it reads. (This is the basis for loopdoc §16 step 2 "a fused group
  is ordered as a unit", and for the rfactor-feeds-a-member ordering — see
  [ordering](ordering.md).)

The *order* of members within the group, and the order of stages within the
emitted body, are covered in [ordering](ordering.md). The legality checks
(`validate_fused_pair`, dependency, etc.) are in [legality](legality.md).
