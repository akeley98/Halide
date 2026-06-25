# split / fuse / reorder / tile

_Part of the [src_doc set](README.md); sections keep their global numbers (§1–§14), and cross-file references are written as "§N"._

## 10. split / fuse / reorder / tile: rewriting the dimension list

Backs loopdoc §9. These directives never touch the producer/consumer graph or
the scheduling *levels*; they only mutate one stage's representation of its own
loops.

### The representation: `dims` and `splits`

A stage's schedule (`src/Schedule.h`) holds two relevant vectors:

    // src/Schedule.h ~446
    struct Dim { std::string var; ForType for_type; ... };   // one entry per loop
    // src/Schedule.h ~332
    struct Split { std::string old_var, outer, inner; Expr factor;
                   enum SplitType { SplitVar, RenameVar, FuseVars } split_type; };

`StageSchedule::dims()` is the ordered loop list, **innermost first**, and always
ends with the `Var::outermost()` sentinel. `splits()` records the split/rename/
fuse operations in application order (used later by bounds inference to relate
the new vars to the original ones). For `print_loop_nest`, what matters is the
`dims` list: `build_provide`/`build_produce_definition` in
`src/ScheduleFunctions.cpp` (see §6) emit one `For` per `Dim`, outermost
first (the reverse of `dims`). So the only structural lever the transforms have
is how they edit `dims`.

### split (`Stage::split`, `src/Func.cpp` ~1076)

    // ~1117: find old in dims, then
    dims.insert(dims.begin() + i, dims[i]);   // duplicate the slot
    dims[i].var     = old + "." + inner;      // innermost copy
    dims[i + 1].var = old + "." + outer;      // just outside it

So `old` at position `i` is replaced by two adjacent dims — `inner` at `i`
(innermost), `outer` at `i+1` — and a `Split{old,outer,inner,factor,SplitVar}`
is appended. Net: `dims` grows by one, i.e. one extra `For`. The new names are
the dotted `old.inner` / `old.outer` seen in raw output. Backs `split_basic.cpp`.

### fuse (`Stage::fuse`, `src/Func.cpp` ~1308)

    // ~1331: erase the outer dim
    dims.erase(dims.begin() + i);             // outer removed
    // ~1347: rename the inner dim's slot to the fused name
    dims[i].var = inner + "." + fused;        // fused takes inner's position

`outer` is removed and `inner`'s slot is renamed to the fused var (covering the
product of the two extents); a `Split{..., FuseVars}` is appended. Net: `dims`
shrinks by one, i.e. one fewer `For`. Backs `fuse_basic.cpp`.

### reorder (`Stage::reorder`, `src/Func.cpp` ~1813)

    // ~1822: record each listed var's current position
    for i: idx[i] = position of vars[i] in dims;   // user_error if not found
    // ~1870: place the listed vars into the SORTED set of those positions
    sorted = sort(idx);
    for i: dims[sorted[i]] = dims_old[idx[i]];

So `reorder` permutes **only the slots the listed vars currently occupy** —
unlisted dims keep their positions — filling those slots in the given
innermost-first order. The `user_assert(found)` at ~1831 is the
"could not find var … to reorder" error backing `neg_reorder_bad_var.cpp`;
duplicates are rejected at ~1838. Note `dims` ordering is the *only* thing
reorder changes.

### Why a pure-serial reorder is invisible

The `For` printer (§6) emits `op->for_type` and `simplify_var_name(op->name)`,
and prints `" in [min, max]"` only for constant bounds. The test harness's
`canonicalize.py` then drops the var name entirely and drops constant bounds.
A serial-loop `reorder` changes only names and the order of otherwise-identical
`for` lines, both erased — hence loopdoc §9's "invisible except through a
topological consequence". The consequence is real because `compute_at`
injection (§7) matches the *level name* in the post-reorder `dims`: moving
a dim inward/outward moves the loop a producer is filed under, changing how many
site-func loops land inside its `consume`. Backs `reorder_topological.cpp` vs
`reorder_baseline.cpp`. (A loop-type change — `parallel`/`vectorize`/`unroll`,
which set `Dim::for_type` — *is* kept by both the printer and canonicalizer, so
reordering typed loops would be visible; that is a later milestone.)

### tile (`Stage::tile`, `src/Func.cpp` ~1754)

The two-var `tile` is implemented as two `split`s followed by a `reorder` of the
four resulting vars to `{xi, yi, xo, yo}` (innermost first), exactly as loopdoc
§7 states. Net: `dims` grows by two. Backs `tile_basic.cpp`.

### Sites are matched post-transform

Because `compute_at`/`store_at` resolve their level by matching the name against
the site func's `dims` at scheduling time, the transformed vars are the legal sites,
and consumed vars are gone. `g.compute_at(out, x)` after `out.fuse(x, y, xy)`
fails the `ComputeLegalSchedules` lookup (§7) since no loop named `x`
remains — only `xy` (and `outermost`/`root`). Backs
`neg_compute_at_fused_away.cpp` and `split_compute_at.cpp`.
