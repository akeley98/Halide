# compute_with: legality

_Part of the [src_doc set](../README.md); backs loopdoc §14 (compute_with). The
compute_with topic is split into [fused_groups](fused_groups.md),
[growth](growth.md), [member_sites](member_sites.md), [ordering](ordering.md),
[legality](legality.md)._

Two kinds of legality: `compute_with`'s **own preconditions** (below), and a
producer's **`compute_at` legality** inside the group, which is just the §7
enclose-every-use rule (not a new rule).

## compute_with's own preconditions

### Matching loop nests down to `v` (`src/ScheduleFunctions.cpp` ~2460)

`validate_fused_group_was_legal` checks each pair against the *resulting*
dimension lists (`def.schedule().dims()`), not scheduling provenance:

* the fuse var must be found in each stage's dims via `var_name_match`, else
  *"cannot find `v` in …"* (`neg_compute_with_mismatch.cpp`);
* `n_fused = dims.size() - start_fuse - 1` (dims from `v` outward, ignoring the
  synthetic `__outermost`) must be equal for both, else *"# of fused dims … do
  not match"* (`neg_compute_with_dim_count.cpp`); and
* each corresponding shared `Dim` must match in `var` name, `for_type`,
  `device_api`, and `dim_type` (~2496–2514). So loops *below* `v` and all extents
  are free, but the shared prefix must agree on name and loop *kind* (`dim_type`
  encodes Var-vs-RVar — see the RVar-kind DISCOVERED DOC GAP).

### Same compute level for all members (~2477)

A single `user_assert(func_1.schedule().compute_level() == func_2.schedule().compute_level())`
per fused pair, erroring *"the compute levels of f.s0 (…) and g.s0 (…) do not
match"*. Pairwise `LoopLevel ==` transitively forces *every* member of a
connected group to one compute level — which is why the group injects as a single
nest at one level (`InjectFunctionRealization` is built with
`compute_level(funcs[0].schedule().compute_level())`, ~1190, and only matches
*that* level). Backs `neg_compute_with_level_mismatch.cpp` /
`compute_with_two_parents_at.cpp`. **It constrains only the compute level** — not
`store_level()` (verified: `f.compute_at(out,y).store_root()` fused with
`g.compute_at(out,y)` is legal, printing a `store f:` node at root). The same
site (~2455–2474) also requires the child to have **no specializations**, neither
stage **inline**, and neither Func **extern** — separate `user_assert`s.

### Acyclic stage order (`check_fused_stages_are_scheduled_in_order`, `RealizationOrder.cpp` ~210)

Walks **one** Func `f`'s stages in order; for each parent it fuses into, keeps
`max_stage_for_parent[parent] = {f-stage, parent-stage}`. As `f`'s stages
advance, the parent-stage index must satisfy (~221):

    is_correct = (fuse_level.stage_index() > max.parent_stage)
              || (fuse_level.stage_index() == max.parent_stage && are_stages_consecutive);

i.e. **non-decreasing** parent-stage indices, equal allowed only for
**consecutive** `f`-stages (`are_stages_consecutive` resets at any non-fused
stage). The reason is `f`'s unavoidable stage order (`s0` before `s1` …): pinning
an earlier `f`-stage to a *later* parent stage than a later `f`-stage targets is
a contradiction — a cycle — rejected with *"impossible to establish correct stage
order"*. Source basis for loopdoc §14's "non-decreasing parent-stage indices"
(`f.s0`→`g.s1` with `f.s1`→`g.s0` fails; `neg_cwtest_crossing_edges*`). It is
**per Func, per parent** (map keyed by parent name), which is why splitting one
Func's stages across *different* parents (`compute_with_two_parents`) is fine.

One of **three** cooperating ordering guards: `check_no_cyclic_compute_with`
(~181, cross-Func cycles `f` fused into `g` and `g` into `f`); this function
(per-Func vs one parent); and `build_pipeline_group`'s `stage_order` loop (~1755,
the actual sort, with fallback *"There is a cycle inside of the fused group"*).

### No producer/consumer dependency

`validate_fused_pair` (`RealizationOrder.cpp` ~88) asserts the two funcs are not
in each other's transitive call set (`indirect_calls`), erroring *"there is
dependency between …"* (`neg_compute_with_dependency.cpp`).

## A producer's compute_at inside the group needs **no new rule**

It is exactly §7's enclose-every-use principle, evaluated on the **post-fusion**
loop structure. Crucially, `(child, v)` *is* a real site (see
[member_sites](member_sites.md)): a producer computed at a member lands at that
member's position. So computing a producer at a child is **legal when every use
of it lies within that child**, and illegal when a use lies outside (e.g. it is
also read by the parent), in which case Halide lists the enclosing member(s) as
the legal locations. `neg_compute_with_producer_at_child.cpp` is illegal because
its `input` is read by **both** `f` and `g` — not because naming a child is
categorically illegal.

> Earlier this file (and loopdoc §14) claimed "naming a child is always illegal —
> the child owns no loop there". That was wrong; corrected per
> [member_sites](member_sites.md) and verified (`/tmp/cwverify.cpp`) for
> `compute_at`/`store_at`/`hoist_storage`. The loopdoc §14 rewrite is the open
> doc-gap fix tracked in `progress.txt`.
