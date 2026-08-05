# A Concise Guide to CPU Scheduling in Halide (for LLMs)

This is a compact, practical guide to writing a fast CPU **schedule** for a
Halide pipeline. It assumes you already understand the **algorithm** of the
pipeline. Your only job is to decide *when, where, and how* each `Func` runs.

The guide is in six parts:

- **Part 1 — Foundations.** The mental model, the inline-by-default trap, the
  directive reference. Read once.
- **Part 2 — The good shape.** What a well-scheduled pipeline looks like, and
  the small set of patterns that get you 95% of the way.
- **Part 3 — Diagnose and iterate.** The dev loop, the profiler, and the
  `.stmt` file. Use these every iteration.
- **Part 4 — Recipes.** Distinct patterns for sliding windows, tiling,
  pyramids, stencil chains, histograms, sibling-Func fusion.
- **Part 5 — Pitfalls.** The traps you'll hit: recurrences, recompute
  multipliers, parallel-loop placement, expensive producers in reductions.
- **Part 6 — Reference.** Checklist, worked example, common mistakes, when
  in doubt.

## Section index (canonical, for citation)

These are the only valid §-numbers in this guide. If you cite a section,
cite one from this list verbatim — do not invent §-numbers (e.g. there
is no §3.55).

- §1 The mental model
- §2 The default schedule is INLINE
- §3 Directive reference
- §4 The 95% schedule: one outer parallel loop + vectorize stride-1
- §5 Inline cheap Funcs; schedule only the ones that earn it
- §6 Exceptions: when to break the 95% shape
- §7 Benchmarking hygiene — CRITICAL
- §8 The profiler is your primary tool
- §9 Reading `.stmt` for vectorization shape
- §10 Sliding window for stencils
- §11 Tiling and storage layout
- §12 Pyramids
- §13 Long stencil chains: periodic `compute_root` checkpoints
- §14 Histograms / scatters
- §15 `compute_with` for sibling Funcs (advanced)
- §16 True axis-level recurrences
- §17 The parallel loop must be OUTERMOST
- §18 `compute_at` recompute multipliers
- §19 Expensive producers inside RDoms: hoist them OUT
- §20 Common mistakes catalog
- §21 Pre-flight checklist
- §22 Worked example
- §23 When in doubt

---

# Part 1: Foundations

## 1. The mental model

- A `Func` is a pure mathematical definition. Every call site is a
  mathematical lookup, not a side-effecting call.
- A `Func` may have a *pure* definition `f(x, y) = ...` and optional *update*
  definitions `f(x, y) += ...` using an `RDom`. Each stage is a separate
  loop and is scheduled separately via `f.update(n)`.
- **Bounds inference** computes what region of each producer is needed and
  pads as required. You usually don't set it by hand, but you often need to
  add `BoundaryConditions::repeat_edge(input)` on inputs that are read with
  offsets.
- The **schedule** decides, for each `Func` (and each stage):
  - *Where* it is computed (inlined, at some loop of its consumer, at root).
  - *Where* its storage is allocated (`store_at`, which can be coarser than
    `compute_at` for a sliding window).
  - *How* the loops are organized: tiled, split, reordered, parallelized,
    vectorized, unrolled.
  - *How* its storage is laid out (`reorder_storage`).

## 2. The default schedule is INLINE

**The default Halide schedule is to INLINE every `Func` into its consumer.
It is NOT `compute_root`.**

If you make no scheduling calls on a non-trivial multi-stage pipeline, one
of three things happens:

1. The compiler takes forever or runs out of memory (exponential inlining
   blowup).
2. The compiled code is absurdly large and slow.
3. The algorithm compiles but produces enormous redundant recomputation.

So a reasonable first step is: apply `.compute_root()` to
every `Func` that is not a trivial, cheap, single-use expression.
This gives you a slow-but-working baseline, which you optimize from there.

But don't stop there. A fully-materialized chain of `compute_root`
intermediates is a *starting point*, not a good schedule — see §4. And
don't go the other way either: sprinkling `compute_at` on every Func is
a common LLM failure — see §5.

## 3. Directive reference

Scheduling uses a fluent API on `Func`. Everything below is a method of
`Func` (or, for update stages, `Func::update(int)`).

### Where to compute / store

- `f.compute_root()` — compute all of `f` once, before any consumer.
- `f.compute_at(g, var)` — compute just enough of `f` inside the loop over
  `var` of `g`, for each iteration of `var`.
- `f.store_at(g, outer_var).compute_at(g, inner_var)` — allocate storage at
  `outer_var` (so it's reused across inner iterations, possibly as a
  circular buffer / sliding window), but compute new values at `inner_var`.
- `f.hoist_storage(g, outer_var)` — hoist *just the allocation* up to
  `outer_var`'s loop level (reusing the buffer across inner iterations) but
  keep the compute schedule unchanged. Use when you want the freshness of a
  fine-grained `compute_at` without paying alloc/free on every iteration.
  **Critical rule: hoist only up to the loop that contains the parallel
  loop, never above it.** If `g` is `parallel(yo)`, the alloc must stay
  inside `yo` so each thread has its own buffer; lifting it past the
  parallel loop turns the buffer into shared state and causes a race.
  `hoist_storage_root()` only works when no enclosing loop is parallel.
  Pattern: `f.compute_at(g, inner).hoist_storage(g, parallel_var)`.
- `f.fold_storage(var, K)` — circular buffer of `K` slots in `var`. Use
  with sliding windows when only the last few values along `var` are
  needed at any time.
- `f.store_in(MemoryType::Stack)` — allocate on the stack. Fast, but only
  for small fixed-size allocations.

### Loop shape

- `f.split(x, xo, xi, factor)` — split `x` into outer `xo` and inner `xi`
  with `x = xo*factor + xi`. `factor` is usually a compile-time constant.
- `f.tile(x, y, xo, yo, xi, yi, tx, ty)` — two splits plus a reorder.
- `f.fuse(a, b, t)` — collapse two **adjacent** loop variables in the loop
  nest into a single loop `t`. Adjacency matters: if `a` and `b` are not
  adjacent (some other var is between them in the nest), `reorder` first.
- `f.reorder(a, b, c, ...)` — change loop nesting, **innermost first**.
  The LAST argument becomes the OUTERMOST loop. Easy to get wrong.
- `f.reorder_storage(a, b, c, ...)` — change memory layout,
  **innermost-dim-first** (i.e. `reorder_storage(c, x, y)` makes `c` the
  stride-1 axis).
- `f.bound(x, min, extent)` — promise at compile time that `x` only ever
  ranges over `[min, min+extent)`. Often **required** to get fixed-size
  vectorize/unroll on the output, and to enable many optimizations.

### Execution

- `f.parallel(var)` — run iterations of `var` on the thread pool.
- `f.parallel(var, task_size)` — block `var` by `task_size` first. Use
  this when the outer loop has many small iterations and you want fewer,
  larger tasks.
- `f.vectorize(var, factor)` — produce SIMD code of width `factor`. Prefer
  `const int vec = natural_vector_size<T>();` — for `T = float`, that's 8
  on AVX2, 16 on AVX-512.
- `f.vectorize(var)` — vectorize by the full extent of `var` (only valid
  after splitting to a small inner).
- `f.unroll(var)` or `f.unroll(var, factor)` — unroll the loop.

### Wrappers

- `f.in(g)` — introduce a wrapper of `f` used only by `g`. Lets you
  schedule the read pattern of `f` per consumer (e.g. stage into a tile
  in registers).
- `f.clone_in(g)` — a fresh, duplicated copy of `f` used only by `g`,
  with its own independent schedule. **Key use case:** a Func is
  consumed by multiple stages with different access patterns. Clone for
  one consumer; schedule that copy independently from the others.
  Inversely useful: clone for the *secondary* consumer and leave the
  clone inlined, so the original is now consumed by exactly one stage
  and can be tightly scheduled around that consumer.

### Tail strategies (for splits where the extent isn't a multiple of factor)

- `TailStrategy::RoundUp` — fastest; extends the loop and does extra safe
  work. Only valid if the producer can be evaluated past the original
  extent (e.g. with a boundary condition).
- `TailStrategy::ShiftInwards` — the default; shifts the last tile so it
  overlaps the previous one. Safe for pure Funcs.
- `TailStrategy::GuardWithIf` — adds an if-check; slowest but always safe.

### Reductions

- `RDom r(...); f(x) += g(r, x);` — a reduction. The accumulation along
  the RDom axis is serial by default.
- `f.update().rfactor(r, r_outer)` — factor a reduction across an axis so
  the outer part becomes parallelizable.

### Code size and IR clarity

- `f.never_partition(var)` / `f.never_partition_all()` — disable Halide's
  *loop partitioning* (splitting a loop into prologue + steady-state +
  epilogue to specialize the steady state). This is a code-size /
  code-clarity knob, not a parallelism knob. Use when:
  - The steady-state simplification doesn't actually buy anything (e.g.
    a y-axis boundary condition is loop-invariant for inner x).
  - You want a cleaner `.stmt` to read while iterating.
  - Binary size or icache pressure matters.

### Diagnostics

- `Target::Profile` (set via `HL_TARGET=host-profile`) — the runtime prints
  a per-Func table with active time, parallelism, allocations, recompute
  ratios, and explicit performance warnings. **Your primary diagnostic
  tool — see §8.**
- `print_loop_nest()` or reading the generated `.stmt` file — useful
  mainly to verify vectorization shape (§9).

---

# Part 2: The good shape

## 4. The 95% schedule: one outer parallel loop + vectorize stride-1

*Use when:* writing the first non-trivial schedule for any pipeline. Almost
every Halide pipeline starts here, and most stay here.

This shape is the right answer for the vast majority of pipelines:

```cpp
const int vec = natural_vector_size<float>();
Var yo, yi;
output.split(y, yo, yi, /*strip*/ 16 or 32)
      .reorder(x, yi, c, yo)              // vectorized axis innermost; c innermost for 3-chan
      .vectorize(x, vec)
      .parallel(yo);

// Every non-trivial intermediate computes inside the output's parallel loop:
producer_1.compute_at(output, yo).vectorize(x, vec);
producer_2.compute_at(output, yo).vectorize(x, vec);
```

The shape has three ingredients (these are the *components* of the
pattern, not a priority order — for "what to fix first" given a
profile, see §8):

1. **Vectorize the stride-1 axis** (usually `x`). Inner loop becomes SIMD.
2. **Parallelize an outer axis** with enough iterations:
   `parallel_tasks ≥ cores` is the floor; somewhere between 1× and 4×
   cores is usually fine for evenly-balanced work. On 64 cores with a
   2560-row image and a 32-row strip you get 80 tasks (≈1.25× cores) —
   that's plenty when each task does similar work. Push toward 4×
   cores only if tasks are uneven and you're seeing low `active
   threads` despite enough total work. If the natural outer dim is too
   short to give even 1× cores, tile and `fuse(yo, xo, t).parallel(t)`.
   See "Task-count tuning" below for diagnosing the two failure modes.
3. **Every non-trivial intermediate `compute_at`s inside the output's
   parallel loop.** This gives one fused parallel region; each thread
   streams its strip of intermediates end-to-end through cache.

### Task-count tuning

The two relevant numbers from the profiler are `parallel tasks` (per
parallel loop) and `active threads` (per Func). Read both before
deciding to retune.

- **Too few tasks** — `parallel tasks < cores` (profile warns "fewer
  than N available threads") AND `active threads` low: decrease split
  factor, or fuse another axis (channels, an outer tile dim) into the
  parallel var. Example: `output.split(x, xo, xi, 64).parallel(xo)`
  gives 24 tasks on 64 cores → fuse `c`: `fuse(c, xo, t).parallel(t)`
  → 72 tasks.
- **Too many tasks** — `parallel tasks ≫ cores` AND per-task `heap
  allocs` shows up in the profile: use `parallel(var, task_size)` to
  block multiple iterations per task. Example: 2560 tasks on 64 cores
  spending real time in malloc — `parallel(y, 16)` blocks 16 rows per
  task, dropping to 160 tasks.
- **Tasks roughly cores .. 4×cores AND `active threads` ≈ cores**: you
  are already in a fine zone. Don't retune for the sake of it.

For small fixed-extent dims (channels): `bound(c, 0, 3).unroll(c)` rather
than vectorize or parallelize on `c`.

**Why this shape is the default.** A `compute_root` per stage with its own
`parallel` becomes one parallel pool phase per stage, with a thread-pool
barrier between, intermediates spilling to DRAM. One fused region keeps
the whole pipeline in cache.

**How to verify:** the profiler's pipeline-level `parallel loops` count
should be 1 (or, for justified exceptions, low). Per-Func, intermediates
should show `parallel loops: 0` (folded into the consumer).

## 5. Inline cheap Funcs; schedule only the ones that earn it

*Use when:* you're tempted to add `compute_at` to a Func and want to
check whether you should leave it inlined instead.

Every `Func` in a good schedule is in exactly one of three states:

1. **Inlined (no directive).** Cheap pointwise expressions with no
   internal reuse and few consumers. No memory cost; often fuses into
   the consumer's vectorized loop.
2. **`compute_at(consumer, v)`.** Funcs whose output is read more than
   once from a small window per consumer iteration (stencils, blurs,
   derivatives).
3. **`compute_root()`.** Multi-consumer at different footprints
   (pyramids), or per-strip recompute too expensive (see §6).

Most over-scheduling by LLMs is unnecessary `compute_at` on cheap,
single-use Funcs (elementwise products, luminance combines, boundary
conditions, normalizations, final combines). Profile symptom: many
tiny Funcs each at <5% time, plus high malloc/free overhead.

**Rule of thumb:** if the Func's RHS is one line and it has one
consumer, don't schedule it. Add `compute_at` only when the Func is
reused within a small window or it's expensive enough to show up in the
profile *before* you add a directive.

### Don't double-cache: stencil aggregations on an already-cached producer

If you cache `A` so its values are hot across the 3×3 reuse in `B(x,y) =
sum of A(x±1, y±1)`, **do NOT also cache `B`**. `B` reads `A` once per
output pixel and writes once. The reuse you wanted was in `A`'s reads,
not in `B`'s writes. Caching `B` adds a store + load for no extra reuse.

**Concrete check:** every time you consider `compute_at` for a Func,
ask "*does anyone read this Func's output at multiple places?*" If
no, inline it — even if it looks like a stencil aggregation.

Canonical inline-even-though-it-looks-non-trivial: a stencil sum/max
whose input is already cached at an outer level; a `det = A*B - C*C`
combine; a final normalization `out = a / b`.

Canonical earns-compute_at: a Func read at multiple spatial offsets AND
no upstream is cached at the same level; truly expensive per-pixel work
(trig/sqrt/div, many FLOPs) reused several times per output pixel;
pyramid levels with different-footprint consumers.

**Default bias:** schedule the MINIMUM Funcs. Cache the one whose
output is reused in a window; everything upstream inlines into it,
everything downstream inlines into the next cached stage or the output.

## 6. Exceptions: when to break the 95% shape

*Use when:* the §4 default doesn't fit because of the structural
properties listed below.

Some patterns justify a separate `compute_root` (and its own
parallel region) for an intermediate:

### 6.1 Multi-consumer with different footprints

A stage consumed by multiple downstream stages with **different**
access patterns (e.g. a downsampled pyramid level read by both the
next-level downsampler AND the upsampling-back-up path; a LUT read by
two different parts of the pipeline) should `compute_root`. Otherwise
it's recomputed once per consumer.

This rule does NOT mean "`compute_root` every pyramid level." See §12
for pyramid handling — only the down-sampling-pyramid levels that
truly have multiple consumers earn `compute_root`; the up-sampling chain
is single-use and stays compute_at the output.

### 6.2 Strip-axis overlap when the producer is small

If a producer is read by a downstream stage with an offset along the
output's strip axis (e.g. `blury(x, y) = blurx(x, y-1) + blurx(x, y) +
blurx(x, y+1)` while output strips along y), per-strip computation of
the producer needs an expanded footprint per strip — redundant work at
strip boundaries.

If ALL of these hold, promote the producer to `compute_root`:

1. Producer's total materialized size is **small** (say < L3, a few MB).
2. Strip-axis offset access pattern.
3. Producer is cheap enough that one big parallel pass beats redundant
   boundary work × num_strips.

### 6.3 Long stencil chains

For chains of ≥ 8 single-use stencil stages, every-stage-inlined fusion
collapses under per-strip halo growth (the halo grows linearly with
chain length, so per-strip work grows quadratically). Break the chain
with periodic `compute_root` checkpoints — see §13.

### Don't `compute_root` something too big

Estimate `extent_x × extent_y × extent_c × sizeof(T)`. If ≫ L3 (say >
16 MB), the buffer makes a full DRAM round trip. Profile signature: a
Func with `peak mem` ≈ `avg mem` and high % time in `free` (the
single-threaded teardown of the big buffer).

Big-intermediate alternatives:

- **Inline.** If the RHS is cheap, duplicating per consumer beats
  writing gigabytes to DRAM.
- **`clone_in(consumer)`.** One copy per consumer, scheduled
  independently.
- **Sliding window inside the bigger consumer** (§10).

### Parallel-phase cost of `compute_root`

Each `compute_root` + `parallel` is its own parallel region with a
thread-pool barrier (~50 µs per region on 64 cores). Watch the profile's
`parallel loops` count. Don't scatter `compute_root` over cheap
intermediates; small single-use Funcs belong `compute_at` the output
or inlined.

---

# Part 3: Diagnose and iterate

## 7. Benchmarking hygiene — CRITICAL

Halide CPU schedules use all cores. Even one stray binary competing
for CPU can double the reported runtime. The `dh_hl` harness locks
out other harness usage while a profiler is running, but cannot block
all other processes.

If a number is surprising (5–10× worse than expected), do NOT
conclude "my change regressed" — first check for stray processes,
then re-run. Unstable numbers are noise, not signal.

If you chase noisy numbers, you will revert good schedules and keep bad
ones.

## 8. The profiler is your primary tool

*Use when:* every iteration of the dev loop. This is the canonical
"what to fix next" reference for the whole guide; §21 defers to it for
the profile-driven half of the pre-flight checklist.

The profiler emits to `stdout` a per-Func table plus explicit warnings
for known antipatterns, along with JSON statistics.
The `dh_hl` harness captures these information, accessed with tools

* `dh_hl view_benchmark_stdout`

* `dh_hl view_benchmark_warnings`

* `dh_hl json_profiler_stats`

Sample (max_filter):

```
total time: 410 ms  runs: 9  time per run: 45.6 ms
average threads used: 27.5  parallel loops: 5  parallel tasks: 2262
heap allocations: 17289   peak heap usage: 384 MB

 funcs ........... | active     | active  | parallel  | heap | peak | avg  |recompute| notes
                   |            | threads | loops|tasks|allocs|  mem |  mem |  ratio  |
  thread idle      |  9.08 19.9%|  24.9   |      |     |      |      |      |         |
  free             | 16.62 36.4%|   1.0   |      |     |      |      |      |         |
  vert_log         |  6.32 13.8%|  42.0   |   4  | 342 |   1  | 304M | 304M |  1.00   |  1
  output           | 11.84 25.9%|  62.5   |   1  |1920 |      |      |      |  1.00   |
  ├vert            |  0.35  0.7%|  62.3   |      |     |1920  |  80M | 1.2M |  1.75   |
  └maximum         |  1.39  3.0%|  61.8   |      |     |      |  64  |      |  1.00   |

Performance warnings:
 1) vert_log launches 4 parallel loops and shows poor utilization of the
    thread pool. Ensure the parallel loop is the outermost one. ...
```

Make improving the worst column of the hottest func a priority.
As an alternative, you may use `dh_hl json_profiler_stats`
to aggregate information across multiple benchmark runs.
Use `dh_hl new_idea` to save ideas for fixing these bottlenecks.

### Top-line stats

- `time per run` — the thing to minimize.
- `average threads used` — if much less than core count, you're
  under-parallelized. Look for the Func with high `active` time and
  low `active threads`.
- `parallel loops` (pipeline-level) — total `halide_do_par_for` calls
  per run. **Expect only 1** `halide_do_par_for` if you're following
  the 95% schedule (§4). More than 1 means the parallel loop is not
  outermost (almost always suboptimal (§17)), or there is more than 1
  parallel region.  The latter may be intentional (§6), but collapse
  them with `compute_at` if they're not.
- `peak heap usage` — if it's a large fraction of L3 (or larger than
  DRAM bandwidth × runtime), some `compute_root` is too eager (§6).

### Per-Func columns

- `active` / `active threads` — where time goes and how parallel it
  was. Low active threads on a meaningful Func means it's
  under-parallelized.
- `parallel loops` — number of distinct parallel-for invocations
  involving this Func. Should be 1, or 0 if folded into a parent's
  parallel loop. > 1 means the Func has multiple update stages with
  separate `.parallel()` calls (§19) or its pure/update each have
  their own.
- `parallel tasks` — total tasks across the run. Want **at least
  cores** (1× is the floor); 1×–4× cores is fine for evenly-balanced
  work, push higher only if `active threads` is low despite enough
  total work. Tiny task counts ⇒ starvation; huge task counts ⇒
  dispatch overhead. See §4's "Task-count tuning" for fixes.
- `heap allocs` — number of separate allocations. If it scales with
  task count and the Func is small, consider `hoist_storage` or
  `store_in(MemoryType::Stack)`.
- `peak mem` / `avg mem` — peak live allocation and average size per
  allocation. Big peak with one allocation ⇒ `compute_root`. Big peak
  with many allocations ⇒ many concurrent per-task slabs.
- **`recompute ratio`** — total cells produced ÷ unique cells the
  pipeline logically needs. **1.0 is ideal.** > 1.0 means redundant
  work, almost always from `compute_at` inside a tiled consumer where
  the producer's required extent grows per tile (overlapping halos,
  per-slice stencil expansion). 1.5–2× is tolerable; 5×+ is a screaming
  red flag — change compute_at level (§18) or tile axis (§16).
- `notes` — numbered, cross-referenced to the warnings list below the
  table.

### What to fix first (priority order)

1. **Pipeline `parallel loops` > 1** — collapse regions (§4). The
   profiler emits a warning naming the Func.
2. **Any Func with `recompute ratio` ≫ 1** — wrong compute_at level
   (§18) or wrong tile axis (§16).
3. **Top-line `peak heap` ≫ L3** — downsize the largest `compute_root`
   via `compute_at` or `clone_in` (§6).
4. **`free` showing as a top-level cost with `active threads = 1`** —
   single-threaded teardown of a huge `compute_root` buffer. Same fix
   as (3).
5. **Hottest Func has low `active threads`** — its parallel loop isn't
   outermost (§17), or its task count is too small (§4).

This profile alone can drive most iterations. Open it first,
but consider reading `.stmt` (§9) for vectorization-shape questions.

## 9. Reading `.stmt` for vectorization shape

*Use when:* the profile shows a Func that's hot but well-parallelized,
suggesting the per-thread inner loop isn't using SIMD efficiently. Or
you want to confirm a specific compute_at level took effect.

Read `.stmt` mainly to check **vectorization shape** (scatter/gather,
vector width). The profile catches most other things.
The `dh_hl build` tool emits `.stmt` into the `dh_hl workspace_bin` directory.

### What the lowered IR looks like

Vectorized and parallel loops don't survive lowering as keyword-tagged
`for`s:

- **Parallel loops** become `halide_do_par_for(<closure>, 0, <extent>,
  ...)` calls, with a matching `internal func ..._par_for_...` block
  earlier in the file. If you called `parallel()` and don't see a
  matching `halide_do_par_for`, it didn't apply.
- **Vectorized inner loops** disappear entirely — the body's scalar
  ops become vector ops.

Vector op shapes:

- Vector store: `buf[ramp(<base>, <stride>, <lanes>)] = <vec expr>`.
  Unit-stride (`stride == 1`) is what you want.
- Vector load: same shape on the RHS.
- Broadcast: `xN(<scalar>)` where N is the lane count.
- Stack/heap allocation: `allocate <Func>[...] stack` / `allocate <Func>
  ...`.
- Sliding window: allocation has a power-of-2 size in the slid dim and
  loads use `%` or bitwise AND in the index.

### Vectorization checklist

For every Func's hot loop, ask:

1. **Is it vectorized at all?** Stores in the hot loop should look like
   `buf[ramp(base, 1, N)] = ...`. Scalar `buf[scalar_idx] = ...` means
   the inner axis wasn't vectorized. **Common pitfall: update stages.**
   `f.vectorize(x, vec)` only schedules the pure def; each update needs
   its own `f.update().vectorize(x, vec)`. One scalar stage tanks the
   pipeline.
2. **Vectors wide enough?** `N` should equal `natural_vector_size<T>()`
   — 16 for float, 32 for uint16/int16 on AVX-512. Smaller `N` means
   you're running at half/quarter machine width.
3. **Avoidable scatters?** Stores should be to unit-stride ramps.
   `ramp(base, stride≠1, N)` store = N serial scalar stores; usually
   means you vectorized an axis that isn't this Func's stride-1 storage
   axis. `buf[index_vec] = ...` is a data-dependent scatter — see §15.
4. **Avoidable gathers?** Loads should be unit-stride ramps or
   broadcasts. `ramp(base, stride≠1, N)` load = strided gather. Check
   if the producer's stride-1 storage axis matches the consumer's
   vectorized axis; fix with `reorder_storage` if not (§12).
   `f(x*2, y)` is a real stride-2 gather — consider computing the
   producer at a coarser granularity so neighbouring output vectors
   share loads.

**Default:** every stage vectorizes its own stride-1 storage axis at
`natural_vector_size<T>()` width; every hot-loop store and load is a
unit-stride ramp or broadcast.

---

# Part 4: Recipes

## 10. Sliding window for stencils

*Use when:* a producer is read at multiple offsets along the consumer's
strip axis (separable blur, vertical-then-horizontal filter, anything
where row K depends on rows K±1, K±2 of the producer).

When a producer is consumed by a stencil consumer (e.g. a separable
blur), keep its output in cache and amortize redundant work:

```cpp
const int vec = natural_vector_size<float>();
Var yo, yi;
out.split(y, yo, yi, 32).parallel(yo).vectorize(x, vec);
producer.store_at(out, yo)       // allocate at strip level
        .compute_at(out, yi)     // compute one scanline at a time
        .vectorize(x, vec);
```

Halide allocates a small rolling buffer of `producer` scanlines per
strip; each new `yi` computes only the newly needed rows. This is the
separable-blur idiom. Add `.fold_storage(y, K)` if you want a fixed-size
circular buffer.

`store_at(f, v).compute_at(f, v)` at the same `var` is equivalent to
`compute_at(f, v)` alone — the `store_at` does nothing. Drop it.
`store_at` only earns its keep at a strictly outer loop than
`compute_at` (which is what enables circular-buffer reuse).

## 11. Tiling and storage layout

*Use when:* an intermediate is reused many times inside a small region
(2D stencils, transpose, small convs), or a downstream stage reads the
producer with a data-dependent index.

For pipelines where an intermediate is cheap but reused many times
inside a tile (transpose, small stencils, 2D convolutions):

```cpp
Var xo, yo, xi, yi;
out.tile(x, y, xo, yo, xi, yi, 64, 32)
   .parallel(yo)
   .vectorize(xi, vec);
producer.compute_at(out, xo)
        .vectorize(x, vec);
```

Choose tile sizes so each tile fits in L1/L2 for its intermediates.

### Storage layout for data-dependent gathers

If a downstream stage reads a producer with a **data-dependent index**
on some axis (trilinear sampling, bilateral grid lookup, any gather
where the index is a function of the pixel value rather than the loop
variable), that axis should usually be **innermost in the producer's
storage** via `reorder_storage`.

Example: `producer(x, y, z, c)` and a downstream stage reads
`producer(xi, yi, zi, c)` with `zi = cast<int>(val * K)` — `zi`
varies per output pixel, so consecutive output pixels probe adjacent
`z` (and `c`) values. Put `c` and `z` innermost:

```cpp
producer.reorder_storage(c, z, x, y);
```

Now neighbouring `(c, z)` values live in the same cache line.

**Rule of thumb:** make axes indexed by *data* innermost; axes indexed
by *loops* can be outer.

### Inlined boundary conditions hidden inside gathers

If a Func performs many vector gathers and reads through an inlined
`BoundaryConditions::repeat_edge` (or `mirror_image`, etc.) wrapper, the
gather re-evaluates the boundary-clamp expression *per lane*, which the
profiler reports as "more vector gathers than dense vector loads".
Schedule the boundary-condition Func explicitly — typically
`compute_at` the consumer's tile/row, or `compute_root` if it is shared
across many consumers, with a tiled compute (e.g.
`bc.compute_at(consumer, yo).tile(x, y, xi, yi, vec, 4).vectorize(xi)`).
Materialising the clamped buffer once turns the gathers into loads from
real memory, eliminating the per-lane branch on the boundary check.

## 12. Pyramids

*Use when:* the pipeline has a Gaussian/Laplacian pyramid, or any
multi-level downsample-then-upsample structure (local Laplacian,
interpolate, multi-scale anything).

Pyramid pipelines have two chains in opposite directions. Handle them
differently.

**Downsample chain:** `base → downsampled[1] → downsampled[2] → …`.
Each level (l ≥ 1) is consumed twice (the next level down AND the
upsampling path coming back up). That's the multi-consumer case (§6.1):

- `compute_root` every level whose materialized size fits comfortably
  (small upper levels especially).
- The full-resolution base is too big to compute_root. Two options:
  - **Inline** if its RHS is cheap (a pointwise premultiply, a clamp,
    a type cast).
  - **`clone_in`** the base for the first downsample consumer, and
    schedule that clone with a sliding window inside the downsampler.
    This lets one consumer have a tightly tiled view while the others
    inline cheaply.

**Upsample chain:** `interpolated[L-1] → … → interpolated[0] → output`.
Single-use chain — fuse into the output's parallel loop via
`compute_at(output, yo)`. Do NOT `compute_root` upsampling levels —
that forces every level through DRAM and adds a barrier per level.

## 13. Long stencil chains: periodic `compute_root` checkpoints

*Use when:* the pipeline has ≥ ~8 single-use stages, each a small
stencil (3×3 or 5×5 box, gaussian, etc.), in a linear chain.

§5/§4 fusion works for short chains (≤ ~5 stages) or chains of pointwise
stages. It falls apart for LONG chains of *stencil* stages.

Reason: every stencil expands the needed footprint by its half-width.
A 5×5 stencil chain expanded `N` times needs an input footprint of
`+2N` per side. For 30 stages that's 60 pixels of halo per side. If
you `compute_at` every intermediate inside a `yo`-strip, per-strip
work grows quadratically with chain length and boundary work
dominates.

**Fix — group the chain with `compute_root` checkpoints every K stages**
(K = 8–12 for 3×3 / 5×5 stencils on a 2K×3K image):

```cpp
const int K = 11;
for (int j = last; j > 0; j -= K) {
    Func &out = (j == last) ? output : stages[j];
    out.compute_root()
       .tile(x, y, xo, yo, xi, yi, tile_w, tile_h)
       .fuse(xo, yo, t)
       .parallel(t)
       .vectorize(xi, vec);
    // The K-1 stages just before this checkpoint slide inside its tiles:
    for (int i = std::max(0, j-K+1); i < j; i++) {
        stages[i].store_at(out, t).compute_at(out, yi).vectorize(x, vec);
    }
}
```

Each K-stage group becomes its own parallel pass. The barrier cost
between passes is trivial next to the redundant-work savings. This is
the canonical case where multiple parallel regions are clearly worth
it.

## 14. Histograms / scatters

*Use when:* the pipeline has an update of the form
`f(x, y, cast<int>(some_expr), c) += value` — i.e. one of the write
indices is data-dependent.

For `hist(..., bin, ...) += value` where the bin is data-dependent
(e.g. `hist(x, y, cast<int>(val * K), c) += ...`):

- **Do NOT vectorize the spatial axes of the update.** Different
  vector lanes may compute different bins; the scatter becomes
  non-vectorizable, and Halide will refuse, scalarize, or race.
  Vectorize is best for dense, aligned, contiguous writes.
  Consider `unroll` for small inner dims (e.g. `c`) instead.
- **Avoid separately scheduling the pure init.** Treat `hist` and
  `hist.update()` as one unit at the same `compute_at` site.
- **Recommended pattern:** `compute_at` the histogram per-tile of its
  downstream consumer, so each tile owns a small private histogram
  that fits in cache and has no cross-tile races.
- A reasonable default: `hist.update().reorder(c, r.x, r.y, x, y).unroll(c)`.
- If you need to parallelize across the reduction itself, use `rfactor`.

**Caveat — global aggregates.** Histogram-equalization-style pipelines
where the histogram is *upstream* of the output (every output pixel
reads a CDF derived from the global histogram) are different: there
the histogram is a global aggregate that must be complete before the
output starts. That's an exception (§6.1) requiring two parallel
phases — a histogram phase, then an output phase. Don't try the
per-tile pattern there.

## 15. compute_with for sibling Funcs (advanced)

*Use when:* you have a complete §4-shape schedule that's already
profile-clean, AND the profile shows a producer's loads dominating
because two sibling Funcs are independently re-loading it. Don't reach
for this before the basic shape is exhausted.

If two Funcs share a common producer and an identical iteration space,
and you're computing them at the same level of some consumer,
`a.compute_with(b, v)` fuses them into a single loop. Each iteration
produces a value of `a` and a value of `b`, reusing loaded producer
values between them.

Canonical case: derivative Funcs `Ix` and `Iy` that both read from
`gray` — `Ix.compute_with(Iy, x)` loads gray once per x rather than
once per Func.

---

# Part 5: Pitfalls

## 16. True axis-level recurrences

*Use when:* a producer has an update where the new value at position
`k` reads from position `k-1` (or earlier) along the *same* axis.

A *true axis-level recurrence* is when the update at position `k` along
some axis reads from position `k-1` (or `k-2`, etc.) along that *same*
axis. Examples: IIR filters (each `y` row depends on the previous y),
summed-area tables (recurrence on both x and y), prefix scans.

The recurrence axis **cannot be parallelized** — each step reads the
previous step's output.

### 16.1 What is NOT a true axis-level recurrence

- **Staged reductions** where one small bookkeeping dim recurs but the
  spatial dims don't (e.g. log-height max filter: each *slice* reads
  the previous slice; spatial x/y rows within a slice are
  independent). The serial axis is the small bookkeeping dim only;
  spatial axes are free. This is the common case in image-processing
  pipelines and is handled by §16.3 below, not by §16.2.
- **Associative RDom reductions** (`sum`, `maximum`). The accumulator
  dim is serial by default but `rfactor`-able; spatial axes are free.

Rule of thumb: an axis is a true recurrence only when the update reads
back from itself at a different value of *that same axis* via the
consumer-facing indexing (not via the RDom).

### 16.2 The consumer's parallel axis must avoid the recurrence axis

If the producer has a true recurrence on axis A, the consumer's outer
parallel loop must be over a non-A axis (or an `rfactor`-introduced
axis). Then each task owns a full-A slab of the producer with no
redundant recomputation.

Profile signature on violation: producer's `recompute ratio` ≈
`num_tasks` and low `active threads`. Fix: change the parallel axis or
use `rfactor`.

### 16.3 Match the consumer's parallel axis to the producer's parallelizable axis

A more general rule that covers both true recurrences (§16.2) and
staged reductions (the §16.1 carveout).

If a hot producer has any axis it CANNOT be parallelized over —
because of a true recurrence, or a staged-reduction bookkeeping dim,
or any other reason — pick the consumer's outer parallel axis from the
producer's *parallelizable* axes. That way each consumer-parallel-task
owns a complete slab of the producer along the producer's serial
axis, and the producer can be `compute_at` inside the consumer's
parallel loop without redundancy.

Worked example: a Func `vert_log(x, y, c, t)` where `t` is a small
staged-reduction dim (each slice depends on the previous slice), but
`x`, `y`, `c` are all parallelizable. Don't parallelize the consumer
over a fused `(yo, xo)` because that complicates the producer's
placement; instead, parallelize over `(xo, c)` fused, and put
`vert_log.compute_at(consumer, that_fused_axis)`. Each task owns the
full-y, full-t slab of vert_log for one (x-strip, channel) pair, and
the t-recurrence runs serially within the task.

Profile signature on violation: producer with `parallel loops` > 1
(it's been forced into its own separate parallel region because the
consumer's parallel axis crossed the producer's serial dim) AND
`recompute ratio` > 1. Or, if the producer is `compute_root`'d to
work around it: a high `peak heap` and a single-threaded `free`.

## 17. The parallel loop should be OUTERMOST

*Use when:* the profiler shows a Func with low `active threads`
relative to `cores`, or shows `parallel loops` > 1 for a Func that
should have only one.

`parallel(var)` does not lift `var` to the outermost position. It
marks that loop as parallel *in place*. Any serial loop already
outside `var` in the loop nest runs serially and launches a fresh
parallel-for on each iteration — N× dispatch overhead.

**How to check "is my parallel var outermost?":** after all
`split`/`reorder`/`fuse` calls, the parallel var must be the LAST
argument of the final `reorder` (because `reorder` is innermost-first,
so the last arg is outermost), or there must be no var to its outside
in the loop nest.

### Canonical antipattern (WRONG)

```cpp
consumer.split(x, xo, xi, 64)
        .split(y, yo, yi, 32)
        .reorder(xi, yi, c, xo, yo)   // reorder is innermost-first:
                                       // the LAST arg (yo) is OUTERMOST.
        .parallel(xo);                 // BUG: xo is not outermost. yo is.
```

Resulting loop nest, outermost to innermost:

```
for yo:                  ← serial outer (8 iterations on a 2560-row image)
  parallel for xo:       ← parallel-for is launched 8 times, once per yo
    for c:
      for yi:
        for xi:
```

Each iteration of `yo` re-launches the thread pool. 8× dispatch
overhead. Profiler signature: the Func's `parallel loops` count > 1.

### The fix (RIGHT)

Make sure the last arg of the final reorder IS the parallel var:

```cpp
consumer.split(x, xo, xi, 64)
        .split(y, yo, yi, 32)
        .reorder(xi, yi, c, yo, xo)   // xo is now last → outermost
        .parallel(xo);
```

Or, more commonly, you want both yo and xo in parallel — fuse them:

```cpp
consumer.split(x, xo, xi, 64)
        .split(y, yo, yi, 32)
        .reorder(xi, yi, c, yo, xo)
        .fuse(yo, xo, t)              // adjacent in loop nest, can fuse
        .parallel(t);                  // single outer parallel loop
```

For `fuse` to be legal, the two vars must be **adjacent** in the loop
nest. Reorder them adjacent first if they aren't.

Other valid fixes if the structure permits: don't split the extra
axis (just `parallel(y)` and skip the y-split); or
`.parallel(yo)` if yo has enough iterations on its own.

## 18. compute_at recompute multipliers

*Use when:* a Func has `recompute ratio` > 1.0 in the profile and
you're trying to figure out where the redundancy is coming from.

Every axis outside `compute_at(consumer, var)` is a recompute
multiplier for the producer.

With `producer.compute_at(consumer, var)`, the producer is re-evaluated
at every iteration of every consumer loop *outside* `var`. If the
consumer's outer nest is `yo, xo, c, yi, xi` and you
`compute_at(consumer, xo)`, the producer is recomputed per `(yo, xo)`
pair, not per `xo`.

Profile signature: `recompute ratio` > 1 on the producer, often
combined with `heap allocs` proportional to the multiplier.

Spot-check any `compute_at` by asking: *what axes sit outside `var` in
the consumer's full loop nest, and does each one contribute to the
producer's required range?* Axes that do ⇒ recompute. Axes that don't
⇒ free.

Common fixes: don't split the outer axis; move `compute_at` outside
the multiplying axis; use `hoist_storage` to at least save the
allocation; or `fuse` the multiplier into the parallel var.

## 19. Expensive producers inside RDoms: hoist them OUT

*Use when:* the pipeline has an update of the form `out(x, y) += f(g(x,
y, r), ...)` where `r` is a search or blur extent — and the profile
shows the inner producer with `recompute ratio` ≈ |r|.

Special case of §18. Update `out(x,y) += f(g(x, y, r), ...)` where the
RDom `r` is a search/blur extent (nl-means search area, bilateral
grid weights). If a producer P (not indexed by `r`) is `compute_at`
inside the reduction, it gets recomputed once per reduction step:
`P_cost × |r|` work.

Profile signature: P's `recompute ratio` ≈ `|r|` (e.g. 49 for a 7×7
search).

Fix: place P at a loop level *outside* the reduction — at the same
tile level as the consumer. Combine with `hoist_storage` to keep one
allocation reused across inner iterations.

## 20. Common mistakes catalog

A grab-bag of less-categorized traps. Most are corollaries of rules
above.

- **Vectorize factor bigger than the inner extent** — produces scalar
  tail code. Keep factor ≤ natural vector width and `bound()` if
  needed.
- **Vectorizing a tiny fixed axis like `c` (2, 3, 4)** — width-2 SIMD
  is worse than scalar. Use `bound(c, 0, N).unroll(c)` instead, and
  vectorize the large stride-1 axis.
- **Parallel over too small an axis** — `parallel(c)` on a 3-channel
  output gives 3 tasks on a 64-core machine. Fuse with a larger axis
  or pick a different one.
- **Forgetting `bound` on small dims** — without it, Halide can't
  fully unroll `c`.
- **Using `unroll` where you meant `vectorize`** — unroll keeps loops
  scalar; vectorize uses SIMD.
- **Scheduling update stages as if they were the pure stage** —
  `f.parallel(y)` only schedules the pure def. Update stages need
  `f.update(i).parallel(y)` separately. Same for vectorize.
- **Computing a producer inside a consumer loop when the producer is
  shared by multiple consumers** — recomputed per consumer.
  `compute_root` (or shared `compute_at` above all consumers) is
  almost always better.
- **No boundary condition + default tail strategy** — out-of-bounds
  reads or slow tail code. Add `BoundaryConditions::...` on inputs
  read with offsets.
- **Thinking `reorder(a, b)` puts `a` outermost** — it doesn't. Args
  are innermost-first; the LAST is outermost.
- **Vectorizing a scatter** (data-dependent index into the Func being
  written, e.g. histogram bins) — almost always a mistake. See §14.
- **Scheduling a trivial pure def separately** — `f(x, y, z, c) = 0.0f`
  followed by a meaningful update. Schedule the Func once (pure +
  update at the same site); don't give the pure def its own
  `compute_root().parallel(...)`.
- **Blindly using `vec = natural_vector_size<float>() = 16`** — great
  for full-image passes; for small Funcs (a 192-wide grid in x, a
  12-deep in z), width 8 or unrolling can be better.

---

# Part 6: Reference

## 21. Pre-flight checklist

*Use when:* you think the schedule is done and want to verify before
moving on.

**Profile-driven.** For "what to fix first when the profile shows a
problem," follow §8's priority list. Don't duplicate it here.

**Schedule-shape** (read your generator):

1. Every cheap pointwise Func left inlined (§5)?
2. Output parallelized (outer) AND vectorized (inner, stride-1)?
3. Intermediates `compute_at` inside the output's outer parallel loop?
4. `bound` + `unroll` on small fixed extents (channels)?
5. For histograms / data-dependent scatters: didn't vectorize the
   update; pure + update at the same site (§14)?

**`.stmt`** (only when the profile is fine but timing is off):

6. Hot-loop stores are unit-stride ramps at `natural_vector_size<T>()`
   width (§9)?
7. No avoidable strided gathers/scatters (§9)?

## 22. Worked example

*Use when:* you want to see the dev loop end-to-end on a tiny pipeline.

Starting point: a generator with only the algorithm, no schedule.
Three Funcs: `input → blur_y → blur_x → output`.

### Iter 1: compute_root everything

```cpp
blur_y.compute_root();
blur_x.compute_root();
```

Compile, bench. Works, slow. Profile shows it's all serial.

### Iter 2: parallelize and vectorize

```cpp
const int vec = natural_vector_size<float>();
Var yo, yi;
output.split(y, yo, yi, 32).parallel(yo).vectorize(x, vec);
blur_y.compute_root().parallel(y, 32).vectorize(x, vec);
blur_x.compute_root().parallel(y, 32).vectorize(x, vec);
```

Bench: much faster. Profile output (mock):

```
total: 1.20 ms.  avg_threads: 28.5  parallel loops: 3  parallel tasks: 240
peak heap usage: 24 MB

  thread idle | 0.18 ms (15.0%) | 12.0
  blur_y      | 0.72 ms (60.0%) | 30.0 |  1 par_loop |  80 tasks | 1 alloc, 16 MB | recompute=1.00
  blur_x      | 0.18 ms (15.0%) | 30.0 |  1 par_loop |  80 tasks | 1 alloc,  8 MB | recompute=1.00
  output      | 0.12 ms (10.0%) | 30.0 |  1 par_loop |  80 tasks
```

What this tells us (per §8 priority order):

- Pipeline `parallel loops` = 3 → multiple parallel regions with
  barriers between (§4 violation; fix with §10).
- `peak heap` = 24 MB across two big compute_root buffers — both are
  fully materialized before being read once.
- No `recompute ratio` problems and threads are reasonable; the issue
  is the pipeline shape, not parallelism or vectorization.

### Iter 3: collapse to one parallel region with sliding window

```cpp
output.split(y, yo, yi, 32).parallel(yo).vectorize(x, vec);
blur_x.store_at(output, yo).compute_at(output, yi).vectorize(x, vec);
blur_y.store_at(output, yo).compute_at(output, yi).vectorize(x, vec);
```

Bench: faster still. Profile (mock):

```
total: 0.42 ms.  avg_threads: 30.5  parallel loops: 1  parallel tasks: 80
peak heap usage: 240 KB

  thread idle | 0.04 ms ( 9.5%) |  6.0
  output      | 0.38 ms (90.5%) | 31.0 |  1 par_loop |  80 tasks
  ├blur_x     | (folded into output) |               | 80 small allocs, 240 KB | recompute=1.00
  └blur_y     | (folded into output) |               | 80 small allocs, 120 KB | recompute=1.00
```

Pipeline `parallel loops` = 1; peak heap dropped 100×; recompute
stayed at 1.00. Stop here, or tune strip height once more.

## 23. When in doubt

- Start with every non-trivial Func `compute_root()` + sensible
  parallel/vectorize at the output. Then pull producers inward using
  `compute_at`.
- If a change is more than a few lines of scheduling code, test after
  each line.
- Trust the profiler and the `.stmt` file. Don't guess.
