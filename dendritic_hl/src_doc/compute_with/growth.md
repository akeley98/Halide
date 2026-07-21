# compute_with: how the fused nest is built (`build_pipeline_group`)

_Part of the [src_doc set](../README.md); backs loopdoc §14 (compute_with). The
compute_with topic is split into [fused_groups](fused_groups.md),
[growth](growth.md), [member_sites](member_sites.md), [ordering](ordering.md),
[legality](legality.md)._

This backs loopdoc §14's "how the fused nest is built" growth procedure and §16
step 4. The whole group is emitted by `build_pipeline_group`
(`src/ScheduleFunctions.cpp` ~1679); `InjectFunctionRealization` is constructed
with the whole group as its `funcs` vector (in within-group realization order,
see [ordering](ordering.md)).

## Per-stage growth = the `stage_order` loop

`stage_order` (~1755) is a topological sort over the stages' `fuse_level` edges
(see [ordering](ordering.md) for what that order actually is). The loop at ~1808
calls `inject_stmt(producer, stage_nest, fuse_level)` once per stage:

* a stage with an **inlined/root** fuse level (an unfused stage) starts its own
  sibling nest;
* a **fused** stage is spliced into the growing nest at its `fuse_level` — its
  loops down to the fuse level land inside the parent stage's, its below-`v`
  loops remain siblings.

This is exactly loopdoc §14's "inject each stage at its own fuse level". The
`[loopdoc-trace]` `debug(1)` lines added to this function print the `funcs`
vector (`funcs.back()` = the produce-nesting / bounds anchor) and the
`stage_order` with each stage's fuse level — run any fused example's `debug_1`
log to see it (`compute_with_two_parents` is the multi-parent case).

## produce/consume wrapping (~1865)

    for (const auto &i : funcs)                 // produce: funcs[0] innermost,
        producer = make_produce(i.name(), producer);   //  funcs.back() outermost
    for (size_t i = 0; i < funcs.size(); i++)   // consume: same nesting
        if (!is_output_list[i]) consumer = make_consume(funcs[i].name(), consumer);

Each wrap goes *outside* the previous, so wrapping in `funcs` order puts
`funcs.back()` **outermost** and the first-realized member innermost — produce
nesting is the **reverse** of the within-group realization order
([ordering](ordering.md)).

## The shared vs. per-member fused loops

`fused_name` (~1037) inserts a `.fused.` token into each stage's fuse-level loop
var. `substitute_fused_bounds` (~1046) then pins the **non-parent** fused loops
to extent 1 (scheduling points) and `replace_parent_bound_with_union_bound`
(~1601, anchored on `funcs.back()`) gives the parent's loop the union extent.
This per-member structure — and why it makes `(parent, v)` and `(child, v)`
*different* sites — is the subject of [member_sites](member_sites.md). All of it
is bounds-level and the loop *count*/nesting is what survives to the canonical
nest.

## Alignment and guards are bounds-only

`compute_shift_factor` / `ShiftLoopNest` (~1835) apply the `LoopAlignStrategy`
shifts, and the differing-extent guards become `IfThenElse` nodes. `PrintLoopNest`
has **no** `visit(const IfThenElse*)` and never prints bounds expressions, so
none of this appears — consistent with loopdoc §14 "out of scope (bounds-only)".
