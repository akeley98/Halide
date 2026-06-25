# compute_with: fused groups

_Part of the [src_doc set](README.md); sections keep their global numbers (§1–§14), and cross-file references are written as "§N"._

## 14. compute_with: fused groups

Backs loopdoc §14. `compute_with` interleaves two stages into one shared loop
nest. It creates no Function; it records a fusion intent on the **child** stage's
schedule, and the realization-order and schedule-injection passes turn that into
the shared nest. Unlike everything above, the relevant state lives on the
`StageSchedule`, and the structure is built in three cooperating places:
`RealizationOrder.cpp` (grouping + within-group order), `Schedule.cpp`
(the per-stage records), and `ScheduleFunctions.cpp` (the actual loop emission).

### API entry points and recorded state

`Stage::compute_with(LoopLevel, align)` (and the `Func` forwarders) is in
`src/Func.cpp` ~2098. It writes a single field — the child stage's
`StageSchedule::fuse_level()`, a `FuseLoopLevel{level, align}`
(`src/Schedule.h` ~311). `level` is a `LoopLevel(parent_func, parent_stage,
var)` naming where to fuse; `align` is the bounds-only alignment map. Default
`fuse_level` is `inlined()`, meaning "not fused". So a `compute_with` is just
"this child stage carries a `fuse_level` pointing at a parent stage + var."

`FusedPair{func_1, stage_1, func_2, stage_2, var_name}` (`src/Schedule.h` ~536)
is the materialized edge: *(parent stage) is fused with (child stage) at
`var_name`, parent computed first.* `populate_fused_pairs_list`
(`src/RealizationOrder.cpp` ~143) reads each definition's `fuse_level` and pushes
a `FusedPair` onto the **parent** stage's `fused_pairs()` list (init stage if
`stage_index==0`, else the matching update).

### Grouping and within-group order (`RealizationOrder.cpp`)

`realization_order` (~295) does the work behind loopdoc §14's ordering:

* It builds an **undirected** `fuse_adjacency_list` (~127) connecting every
  `func_1`↔`func_2`, then `find_fused_groups` (~38) runs a DFS over it so each
  connected component becomes one **fused group** with a synthetic name.
* `validate_fused_pair` (~88) enforces legality, including the producer/consumer
  rule: it asserts the two funcs are **not** in each other's transitive call set
  (`indirect_calls`), erroring *"Invalid compute_with: there is dependency
  between …"* — this is loopdoc §14's "no dependency" requirement and
  `neg_compute_with_dependency.cpp`.
* The pipeline DAG is then computed with each group collapsed to a **single
  dummy node** (~360): every member depends on the dummy, and the dummy depends
  on all funcs the members call. So the whole group realizes as a unit, after
  everything it reads. Sibling order in the DAG is decided by
  `sort_funcs_by_name_and_counter` (~256) — the **same (prefix, visitation
  counter, full name) tie-break** as loopdoc §6.
* The members **within** a group are then ordered by their position in the
  topological result `temp` (~403). The net effect — confirmed empirically —
  is that the non-parent members come first in the §6 tie-break and the **group
  parent is ordered last**; `funcs.back()` is treated as `group_parent` in the
  emitter (below). This "parent last" holds even when the parent's name sorts
  first.

### Loop emission (`ScheduleFunctions.cpp`, `build_pipeline_group` ~1679)

`InjectFunctionRealization` is constructed with the whole group as its `funcs`
vector (in the within-group realization order). `build_pipeline_group` builds the
shared nest:

* **Body / compute order** is `stage_order` (~1755): a topological sort over the
  stages' `fuse_level` edges, "exploiting that stages of a function form a linear
  order." A child stage depends on its parent stage, so the parent's stages are
  emitted first — hence loopdoc §14's "parent body first, then the others in §6
  order." Each stage is injected by `inject_stmt` at its `fuse_level` (~1822), so
  a child's loops are spliced into the parent's at the fuse level; below that
  level each stays a sibling.
* **produce/consume nesting** (~1865):

      for (const auto &i : funcs)                 // produce: funcs[0] innermost,
          producer = make_produce(i.name(), producer);   //  funcs.back() outermost
      for (size_t i = 0; i < funcs.size(); i++)   // consume: same nesting
          if (!is_output_list[i]) consumer = make_consume(funcs[i].name(), consumer);

  Because each wrap goes *outside* the previous, wrapping in `funcs` order puts
  `funcs.back()` (= the parent) **outermost** and the first-realized member
  innermost — i.e. produce nesting is the **reverse** of the within-group
  realization order. This is exactly loopdoc §14's "parent's produce outermost,
  others nested inside in reverse realization order."

* The shared (parent) loop is the one that survives; child fused loops are turned
  into scheduling-only points whose bounds are replaced to refer to the parent
  loop. `fused_name` (~1037) renames the parent loop var to insert a `.fused.`
  token, which is why the loop prints as `for f.s0.fused.y` (the canonicalizer
  drops the name). `substitute_fused_bounds` (~1046) and
  `replace_parent_bound_with_union_bound` (~1601, using `funcs.back()` as the
  parent) implement the union/bounds behavior — all bounds-only, invisible to the
  canonicalized nest.

### Producer at the fused level must name the parent

Because the surviving shared loop belongs to `funcs.back()` (the parent), and the
child fused loops are collapsed to points referring to it, a producer's
`compute_at` site at the fused level only exists on the **parent**. Computing a
producer at a child's fused-or-above loop fails the ordinary
`ComputeLegalSchedules` enclosure check (§6 here / loopdoc §7): the child loop is
not a real injection site enclosing the parent's use. This is loopdoc §14's
producer rule and `neg_compute_with_producer_at_child.cpp`; the legal-locations
diagnostic lists only `compute_at(parent, …)`.

### Alignment and guards are bounds-only

`compute_shift_factor` / `ShiftLoopNest` (~1835) apply the `LoopAlignStrategy`
shifts, and the differing-extent guards become `IfThenElse` nodes. `PrintLoopNest`
has **no** `visit(const IfThenElse*)` and never prints bounds expressions, so
none of this appears — consistent with loopdoc §14 "out of scope (bounds-only)."

### Legality and loop matching (`src/ScheduleFunctions.cpp` ~2460)

`validate_fused_group_was_legal` checks each pair against the *resulting*
dimension lists (`def.schedule().dims()`), not scheduling provenance — backing
loopdoc §14's "matching loop nests down to `v`" framing:

* the fuse var must be found in each stage's dims via `var_name_match`, else
  *"cannot find `v` in …"* (loopdoc `neg_compute_with_mismatch.cpp`);
* `n_fused = dims.size() - start_fuse - 1` (dims from `v` outward, ignoring the
  synthetic `__outermost`) must be equal for both, else *"# of fused dims … do not
  match"* (`neg_compute_with_dim_count.cpp`); and
* each corresponding shared `Dim` must match in `var` name, `for_type`,
  `device_api`, and `dim_type` (~2496–2514). So loops *below* `v` and all extents
  are free, but the shared prefix must agree on name and loop kind. (For the
  current loopdoc scope everything is serial/pure, so name + count are what bite;
  `for_type`/`dim_type` matching becomes relevant once loop types and RVars enter
  a fused prefix.)

The producer/consumer-dependency rejection (loopdoc `neg_compute_with_dependency.cpp`)
comes earlier, from `validate_fused_pair` in `RealizationOrder.cpp` (~88, the
`indirect_calls` check) — see the grouping discussion above.

Two more preconditions back loopdoc §14's "Legality" list:

* **Same compute level for all group members.** The check is a single
  `user_assert(func_1.schedule().compute_level() == func_2.schedule().compute_level())`
  per fused pair (`src/ScheduleFunctions.cpp` ~2477), erroring *"the compute
  levels of f.s0 (…) and g.s0 (…) do not match"*. So a child's `compute_at` must
  agree with the parent's; `compute_with` does not override it. Because it is
  pairwise on a `LoopLevel` `==`, it transitively forces *every* member of a
  connected group to one compute level — which is why the group injects as a
  single nest at one level (`InjectFunctionRealization` is built with
  `compute_level(funcs[0].schedule().compute_level())`, ~1190, and only ever
  matches *that* level). Backs loopdoc §14's "single injection point" rationale
  and `neg_compute_with_level_mismatch.cpp` / `compute_with_two_parents_at.cpp`.

  **Is it exactly this strict? Yes — and it constrains *only* the compute level.**
  It does *not* compare `store_level()`, so two fused members may have different
  store levels (verified: `f.compute_at(out,y).store_root()` fused with
  `g.compute_at(out,y)` is legal and just prints a `store f:` node at root). The
  same site (~2455–2474) also requires: the child (`func_2`) has **no
  specializations**, neither stage is scheduled **inline**, and neither Func has
  an **extern definition** — all separate `user_assert`s, distinct from the
  compute-level equality.
* **Stage order must be consistent** — and yes, this is precisely an
  acyclicity guard for the stage ordering, checked early.
  `check_fused_stages_are_scheduled_in_order` (`RealizationOrder.cpp` ~210)
  walks **one** Func `f`'s stages in order and, for each parent it fuses into,
  keeps `max_stage_for_parent[parent] = {f-stage, parent-stage}`. As `f`'s stages
  advance, the parent-stage index it targets must satisfy (~221):

      is_correct = (fuse_level.stage_index() > max.parent_stage)
                || (fuse_level.stage_index() == max.parent_stage && are_stages_consecutive);

  i.e. **non-decreasing** parent-stage indices, with *equal* allowed only when the
  `f`-stages are **consecutive** (`are_stages_consecutive` resets to false at any
  non-fused stage). The reason is the hard, unavoidable order of `f`'s own stages
  (`s0` before `s1` …): pinning an earlier `f`-stage to a *later* parent stage
  than a later `f`-stage targets is a contradiction — a cycle in the combined
  order — so it is rejected with *"impossible to establish correct stage order"*.
  This is the source basis for loopdoc §14's "non-decreasing parent-stage indices"
  rule (`f.s0`→`g.s1` with `f.s1`→`g.s0` fails). Note it is **per Func, per
  parent** (the map is keyed by parent name), which is exactly why splitting one
  Func's stages across *different* parents (`compute_with_two_parents`) is fine —
  the targets `g.s0` and `h.s1` are in different map entries.

  It is one of **three** cooperating ordering guards, not the whole sort:
  `check_no_cyclic_compute_with` (~181) rejects cross-Func cycles (`f` fused into
  `g` and `g` into `f`); this function rejects the per-Func-vs-one-parent
  contradiction above with a clear early message; and `build_pipeline_group`'s
  `stage_order` loop (~1755) is the actual topological sort, with its own
  fallback cycle detection (*"There is a cycle inside of the fused group"*) for
  anything the first two miss.

### Per-stage growth = `build_pipeline_group`'s `stage_order` loop

loopdoc §14's "growth procedure" is a user-level paraphrase of `build_pipeline_group`
(above): `stage_order` is the topological sort of all member stages, and the loop
at ~1808 calls `inject_stmt(producer, stage_nest, fuse_level)` once per stage — a
stage with an inlined/root fuse level starts its own sibling nest, a fused stage
is spliced into the growing nest at its level. The `[loopdoc-trace]` debug(1)
lines added to this function print `funcs` (with `funcs.back()` = the
produce-nesting / bounds anchor) and the `stage_order` with each stage's fuse
level; run any fused example's `debug_1` log to see it
(`compute_with_two_parents` is the multi-parent case).
