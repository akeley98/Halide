# Findings: how broken is Halide compute_with bounds inference?

Probes in this directory, run against the locally-built real Halide
(`../../build`). Each builds a fused pipeline and compares its realized output
against the **same pipeline with no `compute_with`** (a fresh build), and dumps
the lowered Stmt to `stmt/*.stmt.txt` so we can see where the `if` guards land.

## Verdict: NOT broken for correctness in any case probed.

### probe_multi_child.cpp — multi-child + chains, members need different extents
`parent(x)=x, child_1(x)=x, child_2(x)=x; output(x,y)=parent(x)+child_1(x)+child_2(y)`.
Because `output` reads `child_2(y)`, child_2 is needed over `[0,H)` while
parent/child_1 are needed over `[0,W)` (W=8, H=5). All correct:

| config | edges | result |
|---|---|---|
| case1a/b | child_1→parent, child_2→parent (both orders) | CORRECT |
| case2p1  | parent←child_1←child_2 (chain) | CORRECT |
| case2p2  | parent←child_2←child_1 (chain, bounds propagate up *and* down) | CORRECT |
| *_2d     | 2-D members, fuse at x | CORRECT |

How it stays correct (from `stmt/case1a_1d.stmt.txt`): the fused loop runs over the
**union** range and is split into **prologue / steady / epilogue** sub-loops,
each with per-member `if` guards. `child_2` is `allocate`d size 5 while
parent/child_1 are size 8, and `child_2`'s write is guarded to `[0,5)` while
parent+child_1 share a guard to `[0,8)` — and `child_1`'s write sits *inside*
parent's guard (the "child_1 must be guarded too because it relies on parent"
point). The guards are load-bearing: a size-5 allocation would be corrupted by an
unguarded `[0,8)` write, but results are exact. Chains propagate the per-member
bounds correctly through the middle func (case2p2: child_1 fused into child_2
still gets parent's `[0,8)` guard).

### probe_levels_x_bounds.cpp — DIFFERENT fuse levels + DIFFERENT extents (the untested corner)
3-D chain `f→g→h`, varying f's fuse level vs g's, with an optional index permutation
on f (`f(z,y,x)`) to skew its needed region. Includes the prime suspect
`Aouter_perm` = f fused *outer* than g (so g's shared loops are collapsed dummies
and f's inner loops re-materialize) *with* extent skew. All CORRECT vs reference,
including `Aouter_perm`.

## The one rejected config (expected, safe)
A chain that flips child/parent direction across stages — e.g.
`f.compute_with(g)` together with `g.update(0).compute_with(f.update(0))` — is a
cross-Func cycle. Halide rejects it up front: "Found cyclic dependencies between
compute_with of f and g" (see ../examples/neg_compute_with_mutual.cpp). This is
the same "overzealous check" that keeps Halide's own disabled
`update_stage_pairwise_zigzag_test` from running — a *rejection*, not a miscompile.

## Conclusion
The "catastrophic bounds bug" hypothesis is **not supported** by these probes.
Halide's prologue/steady/epilogue + per-member guard + per-member allocation
machinery produces correct results for multi-child groups, chains (both
directions), differing member extents, and even differing fuse levels combined
with extent skew. The genuine `compute_with` surprises we found are about loop
**structure** (redundant recomputation, counter-intuitive `(child,v)` site
placement) — not incorrect output. So a "do not use, it's buggy" warning is not
justified by correctness; a note about the structural surprises is.

## Caveats / not yet done
- Correctness is judged by value-comparison vs an unfused reference (distinct
  per-coordinate values to expose index errors). A *benign* OOB write into
  allocation padding could in principle escape this; running under ASan would
  close that gap.
- Did not reproduce the exact #4751 `only_some_are_tiled` OOB (tiling only some
  members of a group) — that specific config is the remaining thing to try if we
  want to chase the historically-fragile case.
- Halide-source tracing of each loop-adjust / `if`-insertion (the original
  suggestion) was not added: the lowered `stmt/*.stmt.txt` already shows the
  adjustments (the `new_min`/`new_max`/`prologue`/`epilogue`/shift lets) and the
  inserted guards directly. Can add compile-time tracing if a per-event view is
  still wanted.
