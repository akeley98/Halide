# Externalized runners, problem-size integrity, and app support — research report

Research note (2026-08-06). Answers the question: the harness (`dendritic_hl`)
currently *owns* Halide build + profiling end-to-end, which is impractical for
real apps that ship their own runner (`apps/gaussian_blur`, `apps/resnet_50`).
This report covers (a) how the build tool works today, (b) what those two apps'
runner environments actually require, (c) designs for an "externalized runner"
bolt-on plus alternatives, and (d) the three anti-reward-hacking side issues
(problem size, input ensembles, algorithm vetting). **Research only — no harness
changes made.** Verifies and extends `resnet50_gap_analysis.md`.

---

## Part 0 — TL;DR

- The build tool is a fixed 4-phase pipeline hard-wired around **one** Halide
  generator + Halide's stock `RunGenMain` driver + `--estimate_all` random
  inputs + a single-pipeline profiler-JSON. Everything downstream (cost model,
  benchmark records, result states) is built on that JSON.
- **Neither** app fits. Both ship a custom `main`, load real inputs from files,
  run their own benchmark loop, and do their own output validation. `resnet_50`
  registers one generator; `gaussian_blur` registers **two** and compiles **37**
  variants into one driver — it violates even the assumption the gap analysis
  said was safe.
- The natural cut point for externalization is **after the harness emits the
  Halide static library + header** (end of phase-1b). Everything after that —
  link, stage inputs, run, benchmark, validate — is where app-specific knowledge
  lives and is what should be delegated to a per-app **adapter**.
- I recommend **two tiers**: a *thin adapter* where the harness still drives the
  interleaved/locked run loop (preserves benchmarking hygiene, requires the app
  to emit the profiler JSON), and a *trusting ingest* path where a fully external
  system hands back a results bundle (needed for `gaussian_blur`-class apps, at
  the cost of hygiene guarantees — those results must be flagged and not
  paired-compared against internally-profiled ones). A cleaner realization of the
  thin tier is a **dlopen shared-object split** (Part 3B): the harness emits a
  `no_runtime` `.so`, a frozen runner loads it — no runner build in the hot loop.
- **Provenance (Part 3.6):** once the runner runs the pipeline, guard against it
  *not* executing the built `.so` by checking the profiler JSON's pipeline `name`
  (already the content-hash `-f` basename, currently unchecked) — evidence emitted
  by the pipeline itself, orthogonal to the Part 6 algorithm fingerprint (`-f` name
  is verified absent from the `.hlpipe`).
- **Problem size (side issue 1):** the size comes from the agent-authored
  `set_estimate` calls consumed by `--estimate_all`. That is both a reward-hack
  vector and a silent-incomparability bug. RunGen *already* supports overriding
  size at runtime (`--output_extents`, per-input `random:SEED:[extents]`), so the
  harness can move problem size out of the schedule and into a harness/problem
  config with no recompile.
- **Ensembles (side issue 2):** because RunGen bounds are runtime, the *same
  compiled binary* can be benchmarked at N sizes with no rebuild. This is cheap
  and folds cleanly into the existing paired-by-batch cost model (pair by
  `(batch, size)`).
- **Algorithm vetting (side issue 3):** scoped as an *honest-agent sanity check*,
  not an adversary defense (the generator runs arbitrary C++). Primary gate:
  **byte-equality of a pre-schedule `.hlpipe` snapshot** — freeze the Pipeline
  before any scheduling directive (`serialize_pipeline`, one line mid-`generate()`)
  and `cmp` it against one golden per root. Empirically (tested against this build)
  the blob is byte-reproducible, invariant to the schedule and to incidental C++
  (source_location is blanked; it compares IR, not text), and catches real
  algorithm changes — so plain `cmp` suffices, no deserialize needed. Exact (no FP
  tolerance — handles discontinuous cases like a histogram bucket flip) and
  self-enforcing for "generator params are performance-only." A structural
  deserialize-compare is only a fallback for name-counter drift; runtime
  differential testing (identical seeded inputs, stock RunGen) is the fallback for
  un-freezable apps.

---

## Part 1 — How the build tool works today

Source of truth: `dendritic_hl_lib/build.py` (771 lines), with the toolchain
recipe in `reference_build_commands.md` and the data model in `catalog.py` /
`context.py`.

### 1.1 The two-tool split and locking

- **`init_build`** (`cmd_init_build`, build.py:420-456) selects up to three
  schedule nodes — **target / other / anchor** — and records them in the session
  private workspace's `init_build.json` (catalog-relative paths to each node's
  `generator.cpp` + `generator_parameters.json`). It takes the catalog lock
  because it may *create* the target node from the workspace files.
- **`build`** (`cmd_build`, build.py:518-598) reads that selection **without the
  catalog lock** so its expensive compile does not block other agents. It takes
  the session lock + a shared machine lock; if `--profile N > 0` it upgrades to an
  **exclusive** machine lock (locks.py) so nothing else uses the CPU during
  profiling. Only then does it take the catalog lock to record results.

This split *is* the "benchmarking hygiene" machinery (reason #1 for the harness).
Any externalization design has to decide how much of it to keep.

### 1.2 The 4-phase toolchain (all hard-coded)

Constants pin Halide at `~/Halide` / `~/Halide/build` (build.py:49-57).

- **Phase 1a — ninja, param-independent** (`_write_ninja`, build.py:166-192):
  compiles the node's single `generator.cpp` → `{full_id}_generator` exe, and
  the shared `RunGenMain.o`. Two ninja rules: `gen_exe`, `rungenmain_obj`. The
  `gen_exe` rule takes a single `$in` — **one source file** is structural.
- **Generator-name discovery** (`_discover_generator_name`, build.py:200-218):
  runs the exe with no `-g`, scrapes `available Generators are:`, and **raises
  `HarnessError` unless exactly one** name is found. Treated as a workspace-
  authoring error, not a build outcome (leaves result state untouched).
- **Phase 1b — python subprocess, param-dependent, serial**:
  - `_emit` (build.py:225-233): `./gen -g NAME -o . -f BASE {k=v params} -e
    static_library,c_header,registration[,stmt,conceptual_stmt] target=host-profile`.
    `stmt`/`conceptual_stmt` only for the target node. **`target=host-profile` is
    hard-coded** (CPU + profiler).
  - `_link` (build.py:236-240): `c++ RunGenMain.o BASE.registration.cpp BASE.a -o
    BASE.rungen -lpthread -ldl`. The static-library emit already embeds the Halide
    runtime, so no separate runtime link.
- **Run** (`_run_benchmark`, build.py:243-262): `./BASE.rungen --benchmarks=all
  --estimate_all` with env `HL_PROFILER_JSON_OUTPUT` (+ the secret
  `HL_PROFILER_JSON_TEMPORARY_WARNINGS`). `--estimate_all` sizes every input/
  output from the generator's estimates and fills them with random data — **no
  real input, no file I/O** (the reason RunGenMain is built `-DHALIDE_NO_PNG
  -DHALIDE_NO_JPEG -fno-exceptions`).

### 1.3 What a "run" produces and how it is stored

- `_build_benchmark_obj` (build.py:736-757) parses the profiler JSON and **raises
  unless `pipelines` has length exactly 1**. The stored benchmark record is
  `{hostname, cpu_count, timestamp, parameters, profiler: pipelines[0], warnings,
  stdout}`.
- **Data model** (catalog.py): a `ScheduleNode` owns `generator.cpp` +
  `generator_parameters.json` + `result.txt` + `bench/*.json` benchmarks. The
  node's ID hash covers *both* source files (catalog.py:~1488), so a size/estimate
  change makes a *new node* — every variant is tracked, but size is an opaque
  per-node property the harness never reads. Generator parameters are a JSON list
  of `{name: bool|int|float|str}` objects (`validate_parameters`,
  catalog.py:113-129); **N objects → N binaries** (re-emit + re-link per object).
  A `BenchmarkSet` is a top-level object indexed `[schedule][params index][batch]
  → benchmark full id`.
- **Result state** is build/run success only:
  `unknown < c++ error < halide error < runtime error < success`
  (`_compute_result`, build.py:760-771), monotonic. **There is no output-
  correctness check anywhere** in the harness (confirmed across idea.md/impl.md).

### 1.4 The cost model (cost.py)

- Raw cost = **`wall_time_min`** (fastest run of a record; ~1% CV, robust to
  outliers). Whole-pipeline wall time.
- Comparisons are **paired by batch**: schedules profiled together in one
  interleaved build run share drift, so within-batch differencing cancels
  common-mode noise. 2-way verdicts use a fixed-seed bootstrap percentile CI of
  the median paired difference (`paired_diff_ci`, cost.py:184-199). Anchor ranking
  uses per-batch ratios.
- **The cost model implicitly assumes a fixed problem size and never says so.**
  Comparability is scoped to same-batch + same-machine; size equality is simply
  assumed (idea.md "Cost Comparison Methodology" says nothing about size).

### 1.5 The assumptions, distilled

The gap analysis' A1–A9 hold. The load-bearing ones for this report:

1. One source file, exactly one registered generator.
2. Halide's stock `RunGenMain` is the only driver; run command is fixed.
3. Inputs are random, sized by the generator's estimates (`--estimate_all`); no
   real data, no file I/O, no input-prep step.
4. Benchmark = one profiler-JSON pipeline. Cost = its `wall_time_min`.
5. No correctness/validation notion. Result = "it built and ran."
6. `target=host-profile` (CPU) only.

impl.md's own FUTURE list (impl.md:598-612) already names the missing pieces:
"allow benchmarking without the profiler"; "specify input size / explicit inputs
… passed through to RunGenMain"; "alternative to Halide's RNG"; "GPU target …
really we should just pass args through to the Halide generator and RunGenMain."

---

## Part 2 — The two apps' runner environments

### 2.1 `apps/gaussian_blur`

- **Two generators in one file**: `HALIDE_REGISTER_GENERATOR(GaussianBlurDirect,
  …)` **and** `(GaussianBlur, …)` (generator lines 102, 351). This *by itself*
  breaks the single-generator count `_discover_generator_name` enforces —
  `gaussian_blur` is a harder case than `resnet_50` on the one axis the gap
  analysis called safe.
- **37 variants compiled into one driver.** `GaussianBlur` has three
  `GeneratorParam`s (`factor`, `upsample_order`, `downsample_order`); the build
  sweeps 3×3×4 = 36 combos + the `direct` variant, each a separately-named Halide
  library, all `#include`d via a generated combined header `blurs.h`
  (CMakeLists.txt:22-36, Makefile:14-25). The *app itself* is the comparison
  harness.
- **Custom driver `filter.cpp`** with a bespoke `LockFreeThreadPool` installed via
  `halide_set_custom_do_par_for` (filter.cpp:42-207). The comment is explicit: the
  default Halide pool "is terrible for this app" (few tiny tasks, workers fight
  over the queue). **Benchmarking these blurs under the harness' default pool
  would mismeasure them** — the runner environment is part of the measurement.
- **Real input**: a PNG (`load_and_convert_image`), a `sigma` CLI arg
  (filter.cpp:179-187). Needs `Halide::ImageIO` (libpng/libjpeg); optional
  CUDA/OpenCL LDFLAGS; a GPU schedule path (`device_sync`, `copy_to_host`).
- **Quality metric**: PSNR of each approximate blur vs a `direct` ground-truth
  blur, printing a **Pareto frontier of time vs PSNR** (filter.cpp:164-325). The
  entire point is a time/accuracy tradeoff across *different algorithms* — not one
  pipeline's runtime. It already calls `halide_profiler_report`/`reset`
  (filter.cpp:266-267, 300-301), so partial profiler support exists.
- **No estimates** (0 `set_estimate` calls); sizes come from the real image.

### 2.2 `apps/resnet_50`

(Confirms `resnet50_gap_analysis.md`; recap for contrast.)

- **One generator** (`resnet50`, line 383) — satisfies the count assumption.
- **~90 array-of-buffer inputs** (`Input<Buffer<float,1>[16]>` etc.,
  generator lines 31-66); an enormous, order-sensitive AOT call hand-unrolled
  with macros in `process.cpp` (249-279).
- **Real pretrained weights**: `load_weights.py` downloads torchvision
  `resnet50(pretrained=True)` and dumps hundreds of `.data` + `_shape.data`
  files into a `weight_dir` (needs Python + torch/torchvision + a model
  download). `process.cpp` loads them all (142-242).
- **Seeded random input** (`std::mt19937`, 3×224×224 hard-coded), custom `main`
  with CLI `iterations weight_dir seed output_file`.
- **Benchmarks via `Halide::Tools::benchmark`**, prints `Execution time : %gms` —
  **no profiler JSON, no `HL_PROFILER_JSON_OUTPUT`, no `--benchmarks=all`**.
- **External validation**: writes the 1000-class vector to a file;
  `validate_resnet50_output.py` checks it (Makefile:26-28).
- **No estimates, no GeneratorParams**, schedule inline with "TODO: Actually
  schedule this" — the app most in need of the harness is the least ready for it.

### 2.3 What the two have in common (the real requirements list)

Any app-support design must accommodate, as *optional* capabilities:

1. A **custom driver / `main`** with an app-specific CLI and link recipe.
2. **Real inputs staged from files** and an **input-prep step** (download/convert
   weights; supply an image), plus a data directory passed to the binary.
3. The app's **own benchmark loop** and possibly its **own timing output format**
   (a printed number, not the profiler JSON).
4. An app-specific **execution environment** that is part of the measurement
   (gaussian's custom thread pool; GPU sync).
5. **Output validation / a quality metric** (PSNR; class-vector check) that the
   harness currently has no concept of.
6. Sometimes a **different unit of comparison** (gaussian benchmarks 37 variants +
   a Pareto frontier inside one binary).
7. Multiple / array / structured inputs and **multiple registered generators**.

---

## Part 3 — Externalized-runner designs

The user's spitball: the harness builds only the Halide *generator*, an external
system runs it, links the `.o` into a runner, benchmarks, and the harness trusts
+ ingests the results. Below I make that precise, then give a spectrum.

### 3.1 Where to cut

The current pipeline has three natural seams:

```
 [A] generator.cpp ──1a──> generator exe
 [B] generator exe ──1b(emit)──> {BASE.a, BASE.h, BASE.registration.cpp, BASE.stmt}
 [C] {BASE.a,...}  ──link+run+profile──> benchmark record   <-- app-specific stuff lives here
```

The user suggested cutting at [A] ("build only the generator"). I recommend
cutting one step later, at **the end of [B]** — i.e. the harness owns "source →
Halide static library + header + stmt", and the **adapter owns everything in
[C]**. Rationale:

- The emit step (`-g NAME … -e static_library,c_header,registration,stmt …`) is
  still generic Halide plumbing the harness already does well; keeping it means
  the harness still gets the `.stmt`/`conceptual_stmt` for the target (agent-
  facing value) and still passes generator parameters through.
- All the *hard* app-specific parts — custom link, input staging, custom run,
  custom benchmark, validation — are cleanly on the far side of [B].
- It keeps the profiler-JSON contract as the ingest interface (see 3.3).

An app that truly needs to control emit too (e.g. gaussian's 37-way sweep,
multi-generator) can be handled by letting the adapter *own the emit command as
well* — i.e. the seam is configurable per app: `harness-emits` vs
`adapter-emits`. But the default and simplest seam is end-of-[B].

### 3.2 A per-app "adapter" (the concrete bolt-on)

Introduce an **app manifest** (checked into the app dir, referenced by the
catalog/problem) plus a small set of hook commands. Sketch:

```
# app_adapter.toml (illustrative)
[emit]      mode = "harness" | "adapter"      # who runs the generator emit
            # for adapter mode: a command that, given the generator exe + params,
            # produces the Halide artifacts the linker needs
[prepare]   command = "python3 load_weights.py {data_dir}"   # optional, run once
            outputs = "{data_dir}/ok"                          # cache sentinel
[link]      command = "c++ ... process.cpp {BASE}.a -o {BENCH_BIN} ..."
[run]       command = "{BENCH_BIN} {iters} {data_dir} {seed} {out}"
            emits    = "profiler-json" | "stdout-timing" | "results-bundle"
[validate]  command = "python3 validate_resnet50_output.py {out} {seed}"  # optional
[size]      # see Part 4: harness-controlled extents / ensemble spec
```

The harness supplies `{BASE}`, `{BENCH_BIN}`, `{data_dir}`, `{seed}`, `{iters}`,
and the profiler env vars; the adapter fills in app knowledge. `build.py`'s
`_emit`/`_link`/`_run_benchmark` are already documented monkeypatch seams
(build.py:22-27) — the adapter generalizes exactly those three functions from
fixed constants into per-app config.

### 3.3 The ingest contract (what the harness trusts)

The interface between adapter and harness is **the benchmark record**, whose core
is a single-pipeline profiler-JSON object (`_build_benchmark_obj`). Two channels:

- **`emits = "profiler-json"`** — the adapter's run sets `HL_PROFILER_JSON_OUTPUT`
  and the app driver uses Halide's profiler (calls `halide_profiler_report`). The
  harness ingests exactly as today, gaining nothing new in the cost model. This is
  the *preferred* channel: both apps are close (resnet's `process.cpp` includes
  the profiler headers; gaussian already calls `halide_profiler_report`), they'd
  need to route the profiler to the JSON env var and (for gaussian) narrow to one
  pipeline per record.
- **`emits = "stdout-timing"`** — the adapter run prints a number the harness
  scrapes into a *degraded* benchmark record carrying `wall_time` only (no
  per-func profiler stats, no warnings). This is what resnet/gaussian produce
  today (`Execution time : %gms`, `Direct (...): %d us`). It requires a **second,
  poorer cost path** in the benchmark schema and cost model — but it lets the two
  apps in with zero driver changes. impl.md:600 already lists "allow benchmarking
  without the profiler" as intended future work.

### 3.4 Two tiers of externalization

**Tier 1 — thin adapter, harness still drives the run loop (recommended
default).** The harness keeps ownership of the interleaved/shuffled batch loop,
the exclusive machine lock, the env vars, and the ingest — it just calls the
adapter's `link` once and the adapter's `run` once per (binary, batch). Benefits:
*benchmarking hygiene is preserved* (interleaving + lock + paired-by-batch cost
still hold), which is the harness's #1 reason to exist. Cost: the app must expose
a single-run, harness-invocable benchmark step (one measured run per call), and
ideally emit the profiler JSON. Fits `resnet_50` well after a modest `process.cpp`
adaptation (single run per invocation, route profiler to the env var, keep the
external validate as a `validate` hook).

**Tier 2 — trusting ingest of a fully external bundle.** A new `dh_hl ingest`
tool takes a results bundle (`{profiler-json or timing, params, size, hostname,
cpu, validation verdict, provenance}`) produced by an entirely external system
(e.g. the app's existing `make benchmark_and_validate`). The harness records it
but **cannot vouch for hygiene** — it didn't control interleaving or the machine
lock. Such records must be **flagged `provenance = external`** and **excluded from
paired-by-batch comparisons** against internally-profiled records (they can still
be shown, ranked among themselves, or compared only within their own bundle).
This is the only realistic fit for `gaussian_blur`'s 37-variant / Pareto-frontier
model and for running resnet exactly as-is. It is closest to the user's original
spitball.

**Recommendation:** build Tier 1 first (it's a generalization of three existing
seams and keeps the science honest), and offer Tier 2 as the escape hatch for
apps whose benchmark unit can't be decomposed into single harness-driven runs.
Mark the two provenances distinctly in the catalog so a reader always knows which
hygiene guarantees applied.

### 3.5 Alternative approaches (for completeness)

- **Adapt the apps to the harness instead of vice-versa.** Add `set_estimates`,
  drop the custom driver, benchmark random inputs via RunGenMain. Rejected in the
  gap analysis and I concur: it benchmarks a *functionally meaningless* pipeline
  (random weights, wrong thread pool) and throws away the validation/PSNR that
  make these apps worth studying.
- **Teach RunGenMain to load real inputs + call a validation callback.** RunGen
  already reads real input files (`name=/path.png`, `name=/path` buffers) and can
  dump outputs (`some_output=/path`). For apps whose *only* deviations are "real
  inputs" + "check the output", you could stay on the stock driver and skip a
  custom `main` entirely (see Part 5). This covers a surprising amount of ground
  and needs **no adapter** — but not gaussian's custom pool or resnet's 90-arg
  hand-wiring.
- **Serialized-pipeline hand-off.** Halide has `serialize_pipeline` /
  `deserialize_pipeline` (`src/Serialization.h`). The harness could emit a
  `.hlpipe` and let an external system schedule/compile it. Not useful here: the
  serialized form captures schedule *and* algorithm together, and it doesn't solve
  the driver/inputs/validation problem, which is the actual gap.

### 3.6 Provenance: binding the profiler JSON to the built artifact

Once the harness stops running the pipeline itself, a new *accident* class opens
up: the externalized runner produces plausible-looking profiler JSON without
actually executing the `.so` the harness just built — a stale/cached `.so`, a
wrong path, a hardcoded fallback pipeline, a leftover JSON from a previous run,
or a runner that silently static-linked an old copy instead of `dlopen`ing the
fresh one. Scoped to *accidents*, not adversaries (a malicious runner can read the
expected token and forge a matching JSON — out of scope, per Part 6's threat
model).

**Principle: trust only evidence emitted by the executing pipeline, never the
runner's self-report.** The profiler JSON is written by the Halide runtime *inside*
the pipeline call, so identity baked into the pipeline and surfaced there is a real
attestation; a wrapper claiming "I ran X" is not.

**The mechanism you already have, unchecked.** The harness compiles each
`(node, params-index)` with `-f _emit_basename(full_id, i)` = `g_{ident}_{i}`, and
`full_id` is `ids.schedule_content_hash(source, params_text)`. `-f` sets both the
export symbol *and* the profiler pipeline name — verified: `-f brighten` yields
profiler JSON `pipelines[0].name == "brighten"`. So the JSON *already* carries a
content-hash identity of the exact source+params that was supposed to run;
`_build_benchmark_obj` just never checks it. The single highest-value guard is
nearly free:

```
assert profiler_json["pipelines"][0]["name"] == _emit_basename(full_id, i)
```

This catches a stale `.so` (old source/params → old hash-name), the wrong
variant/params-index, a hardcoded-fallback pipeline, and "source changed but the
runner ran the old binary." It does *not* flag a stale-but-byte-equivalent `.so`
(same source+params) — which is functionally identical, so harmless.

**Orthogonal to the algorithm fingerprint (Part 6).** Verified empirically: the
`-f` name does **not** appear in the serialized `.hlpipe` (two `-f` names →
byte-identical hlpipe; grep finds the string 0 times). The hlpipe keys on the
*internal* Func names (`output`, `blur`, `repeat_edge`), a separate namespace. So
the `-f` provenance token and the pre-schedule algorithm-equality blob never
interfere — you can make `-f` unique-per-build for provenance without perturbing
the algorithm check, and vice versa.

**Freshness.** Independently, treat the JSON as a fresh artifact: the harness owns
the output path, deletes it before the run (it already does for its internal path,
build.py:701-703), and rejects missing / empty / `runs==0` / `funcs==[]` as a
failed run rather than a result. The name check subsumes the leftover-JSON case (a
prior build's JSON carries a prior hash-name → mismatch); do both.

**Layered strength.**
1. *Free / now* — name-equality + well-formedness (exactly one pipeline,
   `name == expected`, `runs>0`, `funcs` non-empty). Catches ~all realistic
   accidents.
2. *Cheap add* — fold `target` (and, once Part 4 lands, the size/params identity)
   into the token, and cross-check the JSON's func-name set against the func set
   the harness expects from the target node's own emitted `.stmt` (also
   pipeline-emitted, so it corroborates schedule identity). Note: the profiler JSON
   records no input sizes, so "right `.so`, wrong size" is a distinct gap the name
   does not close — that's exactly idea.md:327's "embed explicit input sizes in the
   benchmark record" (Part 4).
3. *Strict / optional* — a per-build random **nonce** in the token binds the JSON
   to the exact build invocation (catches even equivalent-stale). Only this tier
   has a cost: since `-f` couples the symbol and the pipeline name, a unique token
   changes the export symbol too, so a fixed-signature `dlopen` runner that resolves
   a constant name breaks — use the metadata/argv runner (reads the name from
   `_metadata`; Part 3B.4) or emit a stable alias symbol. Tiers 1-2 avoid this
   because the content hash is stable across identical source, so a fixed-signature
   runner keeps working *and* you get provenance.

---

## Part 3B — Variant: dlopen shared-object split (recommended realization of Tier 1)

A cleaner way to draw the seam than "harness links a `.rungen`": have the harness
produce a **shared object** (`.so`/`.dylib`) exporting the pipeline, and let a
**prebuilt, never-rebuilt runner** `dlopen` it at runtime. This removes the runner
build from the hot loop entirely — the harness only ever regenerates a `.so`; the
runner binary is frozen. It supersedes the static-link adapter framing of 3.2-3.4
for any app whose runner can be edited once. Verified against the tree
(`rungen_example/brighten.h`, `nm` on `brighten.a`, `src/Module.cpp`).

### 3B.1 Why it works (three enabling facts)

1. **AOT entry points are `extern "C"`** — the header declares `int NAME(...)`,
   `int NAME_argv(void**)`, `const halide_filter_metadata_t *NAME_metadata()`
   under `extern "C"` (brighten.h:40-54); `nm` shows unmangled globals
   (`_brighten`, `_brighten_argv`, `_brighten_metadata`). `dlsym` resolves them
   directly.
2. **`halide_buffer_t` is a stable, header-only ABI** — `HalideBuffer.h` /
   `HalideRuntime.h` need no linking; a runner marshals buffers identically
   whether the pipeline is static or dlopen'd.
3. **The runtime is strippable and weak** — Halide emits `object`/`static_library`
   only (no direct shared-lib; `src/Module.cpp:46-51`), so the harness adds one
   generic `cc -shared` step. The embedded runtime is *weak* and can be omitted
   with `-no_runtime` (brighten.h:62-71).

### 3B.2 The decisive design choice: the runner owns the runtime

Do **not** dlopen a `.so` that carries its own runtime while the runner also has
one. Halide's own header warns that weak-symbol overrides are not reliably honored
across a shared-library boundary (brighten.h:230-235: "if the override is in a
shared library and the halide object files are linked directly into the output,
the builtin versions of the runtime functions will be called … On Linux,
`LD_DYNAMIC_WEAK=1` may help"). That is exactly where a custom `halide_do_par_for`
(gaussian_blur) would silently bind to the wrong copy. So:

- **Pipeline `.so`/`.dylib`: built `-no_runtime`** — pure pipeline code with
  *undefined* `halide_*` symbols.
- **Runner owns the single runtime + all custom overrides** (thread pool,
  profiler hooks, error/print handlers). The `.so`'s undefined symbols resolve
  *upward* to the runner — ordinary dynamic linking, not weak interposition. This
  also makes gaussian_blur's custom pool "just work," since there is only one
  runtime in the process.

### 3B.3 Platform flags (Linux + macOS; Windows ignored)

- **Linux:** `cc -shared pipeline.o -o pipeline.so` (Halide AOT is already PIC);
  build the runner `-rdynamic` (`-Wl,--export-dynamic`) so the `.so`'s `halide_*`
  bind to the runner; `dlopen(path, RTLD_NOW | RTLD_LOCAL)`.
- **macOS:** file may be `.dylib` or `.so` (dlopen ignores the extension). macOS's
  two-level namespace means a `no_runtime` lib's undefined `halide_*` must be
  marked for load-time resolution: build it with `-Wl,-undefined,dynamic_lookup`.
  The main executable exports its symbols to dlopen'd libs by default. Because you
  dlopen an absolute path and a `no_runtime` lib has no dylib deps,
  `@rpath`/`install_name` issues don't arise. Hardened-runtime *library
  validation* could block a differently-signed dylib, but only if the runner is
  built with the hardened runtime + that entitlement — not the case for a local
  research runner (document it, not a blocker).

### 3B.4 Entry-point resolution: fixed-signature vs metadata/argv

- **Fixed-signature** (resnet/gaussian style): `dlsym(h, "dh_hl_gen")`, cast to the
  known prototype, call. Simplest; runner must know the arg list.
- **Metadata/argv** (RunGenMain style, at runtime): `dlsym` `NAME_argv` +
  `NAME_metadata`, read `halide_filter_metadata_t` for arg count/types/dims, build
  `void* args[]`, call `NAME_argv`. **Signature-agnostic** — no runner recompile
  even if the arg list changes. Most future-proof for complex runners; one could
  convert RunGenMain itself into a generic dlopen host and cover all simple apps
  with zero per-app work.

### 3B.5 Why "one-and-done" actually holds

A frozen runner stays valid only if the pipeline's **entry-point name and ABI are
stable**. Both hold under the harness's own premise: it already passes a fixed
`-f` basename (`dh_hl_gen`), and *schedule-only* changes never alter the input/
output signature. The ABI changes only on an *algorithm* change — exactly what
side issue 3 wants to forbid/vet. Caveat: a generator parameter that alters the
signature would break a fixed-signature runner; disallow such params for
dlopen-runner problems, or use the metadata/argv path.

### 3B.6 Bill of work for the (one-time, per-runner) edit

Human-in-the-loop, never in the hot loop:

1. **Excise the static Halide dep from the runner build** (drop `pipeline.a` from
   the link; optionally keep the header for declarations). *The only step whose
   cost scales with runner nastiness* (Perl→Makefile→bash legacy) — but it is a
   dependency *removal*, the easiest kind of legacy surgery.
2. **Add a dlopen shim** (~50-150 lines): dlopen, dlsym entry point(s), expose a
   function pointer with the old prototype; include the header-only Halide
   buffer/runtime headers. Mechanical.
3. **Redirect the call site** to the resolved pointer. If the runner used
   `Halide::Runtime::Buffer` to marshal inputs (resnet + gaussian do), nothing
   else changes. Mechanical.
4. **Settle runtime ownership**: runner links the runtime once and exports it;
   `.so` built `-no_runtime`; one `nm`/`otool -L`/`ldd` pass to confirm a single
   runtime and no unresolved `halide_*`. First-time-per-platform care; trivial
   after.
5. **Parameterize the `.so` path** (CLI/env). Trivial.
6. **Custom overrides** (gaussian pool, handlers) live in the runner — picked up
   automatically under `no_runtime`-in-`.so`; simpler than the static case.
7. **Optional profiler JSON**: runner sets `HL_PROFILER_JSON_OUTPUT` + calls
   `halide_profiler_report` to feed the rich cost model instead of a scraped
   timing number.

Steps 2/3/5/6 are mechanical and identical across runners; step 4 is a first-time
hygiene check; step 1 is the only variable cost, and it's bounded. The harness
side collapses to a fixed generic command: `-e object … -no_runtime` then
`cc -shared`, producing a stable-named, stable-ABI `.so` with no per-app link and
no runner build in the loop.

---

## Part 4 — Side issue 1: problem size comes from the schedule (reward-hack risk)

### 4.1 The mechanism and why it's a problem

Size flows: the agent authors `generator.cpp` including `set_estimate(s)` in
`schedule()`; `build` runs `RunGenMain --estimate_all`, which sizes every buffer
from those estimates and fills random data. The agent controls the whole file
(the node hash covers it), so:

- **Reward hack:** shrink the estimates → smaller problem → lower `wall_time_min`
  → "better" cost, with no real improvement. The cost model reads only wall time
  and never inspects size.
- **Silent incomparability (arguably worse):** even honest agents can drift the
  size between nodes; the paired-by-batch cost model assumes a fixed size and will
  happily compare a 1024² schedule against a 2048² one. idea.md's cost section
  never mentions size; impl.md:602 flags "specify input size / explicit inputs" as
  unbuilt future work.

The prompt *tells* agents to "modify only the schedule" and to use `set_estimate`
for sizes (prompt_common.md:176-182), but nothing **enforces** either.

### 4.2 Fix: move size out of the schedule, into harness-controlled RunGen flags

**RunGen already supports runtime size override**, independent of the generator's
baked-in estimates (verified in `tools/RunGenMain.cpp` / `tools/RunGen.h`):

- `--output_extents=[W,H,...]` sets the output size explicitly.
- Per-input pseudo-files: `name=random:SEED:[extents]`, `zero:[extents]`,
  `constant:V:[extents]`, `identity:[extents]`, or `name=estimate` / `auto`
  (bounds-query a legal input size from the output size).
- `--describe` dumps every argument's name/type/dims so the harness can build
  these flags generically.

So instead of `--estimate_all`, the harness can pass a **problem-level size spec**
(stored on the *problem/catalog*, not the schedule) as explicit extents. The
size then no longer comes from the agent's file at all; changing `set_estimate`
can't move it. This directly kills the hack *and* guarantees cross-node
comparability. It also matches impl.md:610's "just pass args through to …
RunGenMain."

Caveats: (i) the harness must know input arity/dtypes — `--describe` provides
this; (ii) buffer *contents* still matter for some algorithms (histograms/atomics
— impl.md:605-608), so the size spec should also pin the RNG seed and distribution
(`random:SEED:` gives a fixed seed already); (iii) schedules with size-tuned
constants (split factors) still work — size is a problem property and the schedule
adapts to it.

A lighter interim measure (no RunGen-flag work): have the harness *parse the
estimates actually used*, store them on the benchmark record (impl.md:327 already
anticipates this), and (a) refuse to paired-compare records with differing sizes
and (b) flag when a node's size differs from its idea root. That closes the
silent-incomparability hole even before the full override lands.

---

## Part 5 — Side issue 2: an ensemble of problem inputs instead of one size

### 5.1 Why it's cheap

RunGen buffer bounds are **runtime**, not compile-time. The *same compiled
`.rungen` binary* can be benchmarked at many sizes/inputs by re-running it with
different `--output_extents` / per-input specs — **no recompile**. So an ensemble
costs N runs, not N builds. (Contrast with generator parameters, which *do*
require a re-emit + re-link per object — build.py:557,639.)

### 5.2 How it folds into the existing model

- A **problem** defines an *ensemble* = a list of size/input specs (e.g.
  `[512², 1024², 2048²]`, each with a fixed seed).
- Build once per (node, params). In the profile loop, iterate the batch over the
  cross-product `(binary, size)` instead of just `(binary)`. The interleave/shuffle
  and exclusive lock are unchanged.
- The benchmark record gains a `size` (ensemble-member) key. The cost model pairs
  by `(benchmark_set, batch, size)` — a one-line generalization of the current
  batch key (cost.py:75-91 uses `(set_id, batch)`; add the size component).
- Aggregate across the ensemble: e.g. **geomean of per-size ratios vs the
  anchor**, or require the 2-way verdict to be an improvement **at every size**
  (dominance) before calling a win. This is what most reduces overfitting to one
  size and single-size reward hacking.

### 5.3 Limits

Ensembles are natural for RunGen-style apps where size is a pure runtime bound.
Apps that **bake size into the algorithm** (resnet hard-codes 3×224×224;
gaussian derives bounds from the real image) can't be resized by flags — their
ensemble would be "multiple input files / weights", which only the app's own
driver can consume. That again points to the Tier-2 adapter: the *app* defines
its ensemble and reports one record per member.

---

## Part 6 — Side issue 3: vetting that the algorithm didn't change

**Threat model (decides everything below).** The generator is compiled and
*executed* — it can `system("rm -rf …")`, do arbitrary I/O, anything. There is no
security boundary at the generator, so this check is **not** a defense against a
malicious agent; sandboxing is a separate concern. Its job is a **correctness
sanity check for honest agents** — making "correctness is checked" a true
statement rather than theater — by catching the realistic accident: an agent
editing the schedule who inadvertently perturbs the *algorithm* (flips a boundary
condition, tweaks an Expr or a cast, reorders an RDom so the reduction changes,
drops a `clamp`). Sized to that, not to an adversary.

**Why not runtime differential testing as the primary gate.** Comparing a
candidate's output to a reference within an FP tolerance is unsafe for pipelines
with *discontinuous* downstream behavior — e.g. a float result that feeds an
integer histogram: an input drifting 49.999→50.001 flips a bucket, a genuine
correctness change no tolerance can distinguish from noise. So prefer an **exact,
structural** algorithm-equality check; keep runtime testing as a fallback (6.4).

**Why not `conceptual_stmt`.** Tempting (already emitted), but it **already
contains the schedule** — I checked `rungen_example/brighten.conceptual.stmt`:
16-wide vectorized ramps, `parallel` loops, tail handling (lines 79-91). It's a
pre-storage-flattening *lowered* stmt; two legitimate schedules of one algorithm
differ. Reject it as a fingerprint.

### 6.1 The mechanism: byte-equality of a *pre-schedule* `.hlpipe` snapshot

Halide's FlatBuffers serialization (`src/halide_ir.fbs`, `serialize_pipeline` /
`deserialize_pipeline`; `-e hlpipe` emit) captures the front-end IR. The schema
delineates algorithm from schedule — algorithm in `Func.{args, init_def, updates}`
/ `Definition.{predicate, values, args, is_init}` (Exprs over other Funcs/Params) +
embedded constant `Buffer{data:[uint8]}` (so a changed lookup table *is* caught);
schedule in `Func.func_schedule` and, nested per `Definition`, `stage_schedule` +
`specializations`.

**Capture *before* scheduling** (the key choice). Insert one line at the
algorithm/schedule boundary of the generator:
`serialize_pipeline(Pipeline(outputs), getenv("DH_HL_FREEZE"))`, callable
mid-`generate()`. Produce the golden the same way from the seed. This matters
because several directives *legitimately mutate or extend the Func graph* —
`.in()`/`clone_in` insert wrappers, `rfactor` adds a Func + rewrites an update,
`specialize` adds nested `Definition`s, `compute_with` adds `fused_pairs`. A
snapshot taken before any of them is pristine, **exact** (no tolerance — solves the
histogram case), and **target-independent** (pre-lowering; host vs GPU schedule
yields the same blob).

**Empirically, a plain byte-compare (`cmp`) suffices — no deserialize needed.**
Measured against this repo's Halide build (`scratchpad/algeq`):

- **Byte-reproducible** across separate generator runs (same algorithm →
  byte-identical `.hlpipe`).
- **Invariant to the schedule**: two files with a byte-identical algorithm region
  + freeze call but *completely different* schedule code after the checkpoint
  produced **identical** blobs; and a checkpoint blob equalled the `-e hlpipe` of a
  standalone schedule-free compile of the same algorithm.
- **Invariant to incidental C++**: `source_location` is serialized as an empty
  string (`Serialization.cpp:1167`), so line/file shifts don't matter; and it
  serializes the *IR*, not the C++ text — renaming a C++ local variable left the
  blob **identical**. Halide Func names come from a construction-order counter, so
  identical algorithm code (constructed first, as `generate()` does) → identical
  names.
- **Catches real changes**: a different algorithm (`/3`→`/2`) differed; even a
  semantically-equivalent commutative reorder (`a+b+c`→`c+b+a`) differed. That
  conservatism is desirable for a correctness gate, and it provably does *not*
  false-positive on schedule changes.

So the check is `cmp golden.hlpipe candidate.hlpipe` — zero new comparison code.
Two operational notes: the blob embeds `halide_version`/`serialization_version`
(constant per build), so a Halide upgrade means regenerating the golden (same
lifecycle as the existing `profiler_version` gate); and because the freeze is
pre-schedule it **excludes estimates**, so algorithm-equality is cleanly orthogonal
to problem size (Part 4) — changing `set_estimate` cannot perturb the fingerprint.

**`conceptual_stmt` is a viable but inferior alternative.** It is *also*
byte-deterministic, and a schedule-free `conceptual_stmt` is genuinely schedule-free
— so the earlier "won't work" caveat applied only to *scheduled* pipelines. But
`.hlpipe` is strictly better: it is smaller/cleaner (~7.5 KB front-end IR vs an
~11 KB lowered stmt full of bounds-query / `.required` inference boilerplate, plus
profiler instrumentation on `-profile` targets), and it is far easier to snapshot
pre-schedule — `serialize_pipeline` is a one-liner on a live `Pipeline`, whereas
`-e conceptual_stmt` runs *post*-`generate()` and would capture the schedule.

**Fallback — structural deserialize compare — only if byte-equality proves too
brittle** (e.g. an agent constructs a helper Func *before* the algorithm, shifting
the name counter, or you decide to tolerate commutative reordering): deserialize
both and walk the Funcs comparing `Definition.{values, args, predicate}` while
ignoring `func_schedule`/`stage_schedule`/`specializations` and canonicalizing
`Func.name`/`origin_name`/`func_names_in_order`. No built-in does this
(`Function.h:264`'s `operator==` is *identity* equality) — a small tool over
`deserialize_pipeline`. The empirics say the honest-agent / algorithm-untouched
model very likely won't need it.

### 6.2 One golden per root (no per-parameters bookkeeping)

Under the harness's own model — **generator parameters are performance-tuning
sweeps only; all parameter variants implement the same algorithm; agents only
re-schedule** — a whole catalog subtree is re-schedulings of *one* algorithm. So:

- **One golden `.hlpipe` per root**, frozen pre-schedule from the seed (and a
  fresh one at each `new_root`). It is a property of the *root*, not of the node
  and not of the params. Every `(node, params-index)` freeze is checked against
  that single golden — **no provenance tracking, no per-parameters goldens.**
- The "all params variants share one algorithm" invariant then holds **for free**:
  freeze the candidate at each params value and compare to the one golden. If a
  generator param leaks into the *algorithm* (as gaussian_blur's `factor` does),
  that variant's pre-schedule pipeline diverges and the check fires — the
  structural check *is* the enforcement of "params are perf-only." (Gaussian's
  algorithm-changing params are explicitly out of scope for this model.)

This is a clean special case, not a lossy shortcut: a future "jointly optimize a
family of similar algorithms" mode generalizes it to a *set* of goldens with
provenance, without contradicting anything here.

### 6.3 On adversarial evasion (why not to over-engineer it)

Per the threat model this is moot, but for the record: an agent that serializes the
golden at the checkpoint and then "redefines" the output with `output(x,y) = …`
only **adds an overwriting update stage** — Halide computes the dead pure stage
*and* the overwrite (no DCE), so it's strictly *more* work, never a speed win. The
only structural evasion is *rebinding* the Output to a cheaper Func
(`output = cheap;`), and the `generate()`/`schedule()` split is **equally**
vulnerable to it (agent `schedule()` code can reassign outputs too) — so the split
buys no integrity here short of the agent never writing C++ at all. Given the
"honest-agent sanity check" scope, don't build text-locked regions or the split for
this; the checkpoint compare is right-sized.

### 6.4 Fallback: runtime differential testing (for un-freezable apps)

When a pre-schedule freeze isn't available (a custom-driver app whose algorithm
isn't a serializable `Pipeline` at a clean point), fall back to output comparison:
run a reference and the candidate on **identical seeded inputs** and compare.
Feasible on stock RunGen with no custom driver — `name=random:SEED:[extents]`
(deterministic `mt19937_64`) gives byte-identical inputs and `some_output=/path`
dumps the buffer; the harness diffs. Use exact compare for integer pipelines and a
quality threshold (PSNR ≥ X, the gaussian_blur pattern) where approximation is
intended. This is what `filter.cpp`'s PSNR and `validate_resnet50_output.py`
already do — the adapter's `validate` hook (Part 3.2). Weaker than structural
equality for discontinuous cases, hence the fallback role.

**Recommendation.** Primary gate: **byte-equality of a pre-schedule `.hlpipe`
snapshot against one golden per root** (6.1-6.2) — empirically just
`cmp golden.hlpipe candidate.hlpipe`: exact, cheap, no split, no new comparison
code, self-enforcing for "params are perf-only." Fallback: **runtime differential
testing** (6.4) for apps that can't be frozen. Surface either as a new aspect
`algorithm: matched | changed | not-frozen` alongside the existing build/run
result state, composing with the profiler run rather than replacing it.

---

## Part 7 — Suggested sequencing

1. **Cheapest, highest integrity ROI:** (a) store the problem size actually used
   on each benchmark record and gate paired comparisons on size-equality (Part 4.2
   interim), closing the silent-incomparability hole; and (b) assert the profiler
   JSON's pipeline `name` equals the compiled `-f` basename (Part 3.6) — a
   near-free provenance check that works even on today's internal path.
2. **Harness-controlled sizes** via RunGen `--output_extents` / `random:SEED:[…]`
   + `--describe` (Part 4.2). Kills the size reward-hack; unlocks…
3. **Ensembles** (Part 5) — a small generalization of the batch key once size is
   harness-controlled.
4. **Algorithm-equality gate** (Part 6): freeze one golden `.hlpipe` per root
   pre-schedule, byte-compare (`cmp`) each candidate freeze against it; add the
   `algorithm: matched | changed | not-frozen` aspect. Runtime differential
   testing (6.4) only as a fallback for un-freezable apps.
5. **Tier-1 adapter** (Part 3.4), ideally as the **dlopen split** (Part 3B),
   generalizing `_emit`/`_link`/`_run_benchmark` into per-app config; land
   `resnet_50` on it (single-run `process.cpp`, profiler env, external `validate`
   hook), with the Part 3.6 provenance check on every ingested JSON.
6. **Tier-2 trusting ingest** (Part 3.4) with a `provenance=external` flag for
   `gaussian_blur`-class apps (multi-variant, custom pool, Pareto frontier).

Items 1–4 harden the *existing* single-generator path against reward hacking and
are independent of app support; 5–6 are the app-support build-out. Each is usable
on its own.

---

## Appendix — key references

- Build tool: `dendritic_hl_lib/build.py` (phases 1a/1b, emit/link/run, result
  state); recipe in `reference_build_commands.md`.
- Cost model: `dendritic_hl_lib/cost.py` (`wall_time_min`, paired-by-batch
  bootstrap); aggregation in `profiler_stats.py`.
- Data model: `catalog.py` (ScheduleNode/IdeaNode/SessionNode/BenchmarkSet,
  `validate_parameters`); `context.py` (`SessionWorkspace.workspace_source`).
- Design docs: `idea.md` (no algorithm/schedule split, no anti-gaming, cost
  section silent on size), `impl.md:598-612` (FUTURE: profiler-less benchmarking,
  explicit input sizes, alt RNG, GPU, "pass args through to RunGenMain").
- Apps: `apps/gaussian_blur/{gaussian_blur_generator.cpp,filter.cpp,CMakeLists.txt}`
  (two generators, 37 variants, custom pool, PSNR Pareto); `apps/resnet_50/
  {Resnet50Generator.cpp,process.cpp,load_weights.py,Makefile}` (90 array inputs,
  torchvision weights, custom benchmark, external validate).
- RunGen capabilities: `tools/RunGenMain.cpp`, `tools/RunGen.h`
  (`--output_extents`, `--describe`, `--estimate_all`, per-input
  `random:SEED:[…]`/`zero`/`constant`/`identity`/file, output dump).
- Prior note: `human_stuff/resnet50_gap_analysis.md`.
</content>
</invoke>
