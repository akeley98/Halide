# Loop types (ForType): serial / parallel / vectorized / unrolled / GPU

_Part of the [src_doc set](README.md); sections keep their global numbers, and cross-file references are written as "§N"._

## 17. Loop types: `Dim::for_type` and `Dim::device_api`

Backs loopdoc §17. A loop's *type* is a per-dimension property carried on the
schedule and printed as the leading token of the loop line; it is orthogonal to
the loop *structure* the other directives build.

### The state: `Dim`

    // src/Expr.h ~419
    enum class ForType { Serial, Parallel, Vectorized, Unrolled,
                         Extern, GPUBlock, GPUThread, GPULane };
    // src/Schedule.h ~446
    struct Dim { std::string var; ForType for_type; DeviceAPI device_api;
                 DimType dim_type; ... };   // one entry per loop

Every loop dimension holds a `for_type` (default `Serial`) and a `device_api`
(default `None`). The directives below only ever mutate these two fields (and, in
the factor forms, perform a `split` first).

### Printing (what `print_loop_nest` shows)

    // src/PrintLoopNest.cpp ~90 (visit(const For*))
    out << op->for_type << " " << simplify_var_name(op->name);
    ... " in [min, max]" only for constant bounds ...
    out << op->device_api;                    // ~114

    // src/IRPrinter.cpp ~370  operator<<(ostream&, const ForType&)
    Serial->"for"  Parallel->"parallel"  Unrolled->"unrolled"
    Vectorized->"vectorized"  GPUBlock->"gpu_block"  GPUThread->"gpu_thread"
    GPULane->"gpu_lane"  Extern->"extern"
    // src/IRPrinter.cpp ~106  operator<<(ostream&, const DeviceAPI&)
    None/Host -> ""   Default_GPU -> "<Default_GPU>"   CUDA -> "<CUDA>"  ...

So the loop line is `<for_type> <var>[ in [lo,hi]]<device_api>:`. The harness's
`canonicalize.py` drops the var name and constant bounds but keeps the `for_type`
token and the `<device_api>` suffix — so a loop's observable identity is
`(for_type, device_api, position)`.

Crucially, `print_loop_nest` runs only the front of lowering
(`src/PrintLoopNest.cpp` ~167–226) and then walks the IR. It does **not** run
`VectorizeLoops` / `UnrollLoops` (so vectorized/unrolled loops print literally
rather than being expanded) nor the GPU passes `CanonicalizeGPUVars` /
`FuseGPUThreadLoops` (so GPU loops print raw, as scheduled). GPU legality (block
must enclose thread, warp-size limits, thread-count bounds) is enforced only in
those skipped passes — hence out of scope for this path.

### The whole-dimension setters (`src/Func.cpp`)

    // ~1645  serial(v):    set_dim_type(v, ForType::Serial)
    // ~1650  parallel(v):  set_dim_type(v, ForType::Parallel)
    // ~1655  vectorize(v):  set_dim_type(v, ForType::Vectorized)
    // ~1660  unroll(v):     set_dim_type(v, ForType::Unrolled)
    // ~483   Stage::set_dim_type(v, t): find v in dims(); dims[i].for_type = t
    // ~551   Stage::set_dim_device_api(v, api): dims[i].device_api = api

`set_dim_type` (~483) also holds the parallel-`RVar` race-condition legality:
setting a `Parallel`/`Vectorized`/GPU type on an `RVar` `user_assert`s that
`allow_race_conditions()` or `atomic()` is set (and, under `atomic`, proves
associativity). That is a legality concern needing analysis micro does not do —
out of scope; micro models only the loop-nest effect.

### The factor forms imply a split and type ONE half

    // src/Func.cpp ~1665  parallel(v, factor):
    split(v, /*outer=*/v, /*inner=*/tmp, factor);  parallel(v);   // types the OUTER
    // ~1677  vectorize(v, factor):
    split(v, /*outer=*/v, /*inner=*/tmp, factor);  vectorize(tmp);// types the INNER
    // ~1690  unroll(v, factor):
    split(v, /*outer=*/v, /*inner=*/tmp, factor);  unroll(tmp);   // types the INNER

The split reuses `v`'s name for the **outer** loop and a fresh `tmp` for the
**inner**. `parallel(v, n)` then types the outer (`v`); `vectorize`/`unroll` type
the inner (`tmp`). This is the asymmetry in loopdoc §17 and what makes the
inner/outer split order observable (§9): `parallel(x, 8)` prints
`parallel …: for …:` while `vectorize(x, 8)` prints `for …: vectorized …:`.
Backs `fortype_parallel_split.cpp` vs `fortype_vectorize_split.cpp`. (The
`TailStrategy` argument only affects the split's boundary handling — bounds,
normalized away.)

### The type rides the dimension through the §9 transforms

* **split / tile** (`src/Func.cpp` ~1122): the old dim is duplicated in place —
  `dims.insert(dims.begin()+i, dims[i]); dims[i].var = inner; dims[i+1].var = outer;`
  — so **both** halves inherit `for_type` and `device_api`. (Special case ~1126:
  splitting an `Extern` loop forces the outer to `Serial`.) Backs
  `fortype_split_inherit.cpp`.
* **fuse** (`src/Func.cpp` ~1349): the fused loop **reuses the inner dim's slot**
  (`dims[i].var = fused; ` then erase the outer), so it keeps the **inner**
  dimension's `for_type`/`device_api`; the outer's type is dropped. Backs
  `fortype_fuse_inner_wins.cpp`.
* **reorder**: permutes only the `dims` order; the `for_type` stays attached to
  its dim. This is loopdoc §9's second way `reorder` becomes observable. Backs
  `fortype_reorder_typed.cpp`.

### GPU directives (`src/Func.cpp`)

    // ~1896 gpu_threads(tx[,ty,tz]): set_dim_device_api + set_dim_type GPUThread
    // ~1920 gpu_lanes(tx):          ... GPULane
    // ~1926 gpu_blocks(bx[,by,bz]): ... GPUBlock
    // ~1950 gpu_single_thread():    splits Var::outermost() by 1 twice -> block+thread
    // ~1959 gpu(bx, tx, ...):       gpu_blocks(bx...) then gpu_threads(tx...)
    // ~1974 gpu_tile(v, bx, tx, n): split/tile then block(outer)+thread(inner)

Each sets both `for_type` and `device_api` (default `Default_GPU`). `gpu_tile`
is sugar over `split` + block/thread typing (outer=block, inner=thread), backing
`fortype_gpu_blocks_threads.cpp` (explicit) and `fortype_gpu_tile.cpp` (sugar).

### Extent-1 collapse is gated on device (documented, not tested)

    // src/Simplify_Stmts.cpp ~282  visit(const For*)
    } else if (equal(new_min, new_max) && op->device_api == DeviceAPI::None) {
        return mutate(LetStmt::make(op->name, new_min, new_body));   // drop the loop
    }

A 1-iteration loop is removed only when `device_api == None`; the `for_type` is
not consulted. So extent-1 serial/parallel/vectorized/unrolled loops all collapse
identically (§7 elision), but a 1-iteration **GPU** loop survives — e.g.
`gpu_single_thread()` prints its extent-1 block and thread loops. This is
documented in loopdoc §17 but **not tested** in micro: micro has no bounds
analysis, so it learns a loop's extent only via `micro_halide_collapses` (§7),
which is already the answer key — there is no honest GPU-survival test to write.

### compute_with requires matching type on the paired dimensions

    // src/ScheduleFunctions.cpp ~2521 (validate_fused_group_schedule)
    user_assert(d1.for_type == d2.for_type)
        << "Invalid compute_with: for types of dim " << i
        << " of " << func_1 << ".s" << stage_1 << "(" << d1.var << " is " ...
        << ") do not match.\n";
    // ~2525 likewise for device_api

For each pair of shared dims down to the fuse level, `for_type` (and
`device_api`) must be equal — not just the name/count (§14). Fusing a `parallel`
dim with a `vectorized` one errors. Backs `neg_compute_with_fortype_mismatch.cpp`.
