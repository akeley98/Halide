# compute_with: per-member loop sites — `(parent, v)` vs `(child, v)`

_Part of the [src_doc set](../README.md); backs loopdoc §14 (compute_with). The
compute_with topic is split into [fused_groups](fused_groups.md),
[growth](growth.md), [member_sites](member_sites.md), [ordering](ordering.md),
[legality](legality.md)._

## The question this answers

In a fused group, are `(parent, v)` and `(child, v)` — both naming the shared
fused loop — the *same* scheduling site? **No.** They are two different `For`
loops at two different *positions* in the interleaved body, even though they
iterate the same values. A producer `compute_at(member, v)` lands at *that
member's* position. This is the evidence for the corrected loopdoc rule (a child
member **is** a usable site, subject to the ordinary §7 enclose-every-use test —
not "the child owns no loop, so naming it is illegal", which was wrong).

## Worked counterexample

`child.compute_with(parent, y)` (both `compute_root`), producer `g` read only by
`child`, `g.store_root()`, and `g` computed at either the parent or the child:

```
g.compute_at(child, y)                 g.compute_at(parent, y)
-------------------------------        -------------------------------
for fused.y:                           for fused.y:
  for x: parent          # parent        produce g: for y,x: g
  produce g: for y,x: g  # at CHILD pos  consume g:
  consume g:                               for x: parent       # g wraps BOTH
    for x: child                           for x: child
```

Same fused loop, **different injection point**: at the child the `produce g`
sits *after* parent's body and wraps only child; at the parent it sits at the
*top* and wraps both. (Full nests: the two
`examples/human_compute_at_compute_with_child*.cpp`.)

## Why — the control flow (with source + debug evidence)

1. **Each member-stage is built with its own fused loop var.**
   `build_pipeline_group` (`src/ScheduleFunctions.cpp` ~1808) builds each stage
   in `stage_order` with prefix `<func>.s<n>.`; `fused_name` (~1037) inserts
   `.fused.`. So `parent.s0` owns loop `parent.s0.fused.y` and `child.s0` owns
   `child.s0.fused.y` — two distinct loop vars, one per member-stage.

2. **Each fused child stage is spliced into its parent at the fuse level**
   (`inject_stmt`, ~1822). Pre-bounds, the body is literally nested:

       for parent.s0.fused.y:
         for parent.s0.x: parent
         for child.s0.fused.y:        # at the child's position in the body
           for child.s0.x: child

3. **Each non-parent fused loop is pinned to extent 1 — a "scheduling point".**
   `substitute_fused_bounds` (~1046) rewrites the child fused loops; its own
   comment (lines ~1069–1076) is explicit:

   > *"This is the child loop of a fused group. The real loop of the fused group
   > is the loop of the parent function … This child loop is just a scheduling
   > point … rewrite it to be a simple serial loop of extent 1."*

   So `child.s0.fused.y` survives as an **extent-1 loop at the child's position**;
   `parent.s0.fused.y` keeps the union/full extent (the real loop,
   `replace_parent_bound_with_union_bound`, ~1601, anchored on `funcs.back()`).

4. **A `compute_at` producer is injected by matching its compute-level loop name.**
   `g` is *not* a group member; it is injected into the group's body by the
   ordinary `compute_at` pass, which matches `g`'s compute level against `For`
   names. `HL_DEBUG_CODEGEN=3` prints the match:
   - `g.compute_at(parent, y)` → *"Found compute level at `parent.s0.fused.y`"* —
     the outer real loop → `produce g` wraps the **whole** body (parent + child).
   - `g.compute_at(child, y)` → *"Found compute level at `child.s0.fused.y`"* —
     the extent-1 scheduling point at the child's position → `produce g` wraps
     **only** what is inside it, i.e. only child's body.

5. **Final `simplify` deletes the extent-1 `child.s0.fused.y` `For`**, but `g`'s
   `produce`/`consume` (injected *inside* it in step 4) stays at the child's
   position. Hence the two different nests above.

## The rule this establishes

* `(member, v)` for any group member is a real scheduling site, located at that
  member's position in the interleaved body. `(parent, v)` is the top of the
  shared loop; `(child, v)` is the child's spot (after the members before it).
* Computing a producer at `(child, v)` is therefore **legal exactly when the §7
  enclose-every-use rule holds** — i.e. when every use of the producer lies
  within that child's region (see [legality](legality.md)). When the producer is
  also used by another member (e.g. the parent), the child's site does not
  enclose that other use and Halide rejects it, listing the enclosing member(s)
  as the legal locations — which is why
  `examples/neg_compute_with_producer_at_child.cpp` is illegal (its `input` is
  read by both `f` and `g`), *not* because child-naming is categorically illegal.

> Verified against real Halide (`/tmp/cwverify.cpp`): for `compute_at`,
> `store_at`, and `hoist_storage`, naming the child is **legal** when the
> producer is used only within that child and **illegal** when used by another
> member. The earlier "naming a child is always illegal" claim was an
> over-generalization from cases whose producer was used by multiple members.
