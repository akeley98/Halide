# `dh_hl` harness vs. `apps/resnet_50` — Gap Analysis

Research note (2026-08-04) answering the idea.md "how hard would it be
to get the harness to deal with resnet_50's custom `process.cpp`?" Research only;
no harness changes were made. Verbatim sub-agent findings, valid against the
codebase at commit a5f4fefe5.

## Part 1 — What the harness assumes about an app

The harness build/profile driver is `dh_hl_lib/build.py`. Its assumptions, with references:

**A1. Single source file = single `Halide::Generator` in one `generator.cpp`.**
- Each schedule node is one `generator.cpp` file (`_node_entry`, build.py:274-284; `_NodeBuild`, build.py:387-409). Phase 1a compiles exactly that one `.cpp` into a generator exe (`_write_ninja`, build.py:162-188; the ninja `gen_exe` rule takes a single `$in`, build.py:186).
- **Exactly one registered generator.** `_discover_generator_name` (build.py:196-214) runs the exe with no `-g`, scrapes the `available Generators are:` list, and raises `HarnessError` unless the count is exactly one (build.py:210-213). Enforced hard; documented in impl.md:564-589 ("single-generator assumption") and reference_build_commands.md:6-11, 89.

**A2. Fixed 2-phase toolchain: C++ → generator exe → Halide static lib → link against RunGenMain.**
- Phase 1a (ninja): `generator.cpp` → `{full_id}_generator` exe + shared `RunGenMain.o` (build.py:162-188, 505-537).
- Phase 1b (Python subprocess, serial): `_emit` (build.py:221-229) runs the generator emitting `static_library, c_header, registration` (+ `stmt, conceptual_stmt` for the target), then `_link` (build.py:232-236) links `RunGenMain.o + {base}.registration.cpp + {base}.a` into `{base}.rungen`. Rationale in impl.md:536-563.

**A3. The benchmark driver is Halide's stock `RunGenMain.cpp`.** The app supplies no driver of its own. `_RUNGENMAIN_CPP` is `~/Halide/tools/RunGenMain.cpp` (build.py:56); the produced binary is a "RunGenMain"-style standalone (reference_build_commands.md:41-51).

**A4. Benchmarking uses random/estimated inputs — no real input data or weights.**
- `_run_benchmark` (build.py:239-254) invokes `./{bin} --benchmarks=all --estimate_all`. `--estimate_all` means "size every input/output from the generator's estimates and fill with random data." reference_build_commands.md:20-25 spells out that no image I/O is needed and RunGenMain is compiled `-DHALIDE_NO_PNG -DHALIDE_NO_JPEG -fno-exceptions`. impl.md:602 lists "specify input size / explicit inputs" as *future* work.
- Implicit sub-assumption: the generator declares **estimates** on its Inputs/Output so `--estimate_all` can size them.

**A5. `target=host-profile`, hard-coded.** `_emit` always appends `target=host-profile` (build.py:228). GPU/other targets are future work (impl.md:610).

**A6. Profiler emits exactly one pipeline.** `_build_benchmark_obj` (build.py:633-654) reads `HL_PROFILER_JSON_OUTPUT` and raises `HarnessError` unless `pipelines` has length exactly 1 (build.py:637-641). Warnings parsing similarly assumes one generator/pipeline (reference_build_commands.md:89-92).

**A7. Generator parameters drive the build matrix.** Each node carries a `generator_parameters.json` (list of param objects); N objects → N binaries, re-emit + re-link per object (build.py:461, 543-565; `_param_tokens`/`_format_param_value`, build.py:136-155). At least one params object is required (build.py:398-401). Params are passed as trailing `key=value` tokens to the generator.

**A8. Output validation = the profiler JSON only.** There is no functional/correctness check of the pipeline's output. "Result state" is purely build/run success (`_compute_result`, build.py:657-668): `c++ error` → `halide error` → `runtime error` → `success`.

**A9. Hard-coded Halide layout.** `~/Halide/build` and `~/Halide` (build.py:48-56); links `libHalide_GenGen.a`, `-lHalide`, `-lpthread -ldl` (build.py:172-173, 233-236).

## Part 2 — What `apps/resnet_50` actually is

**Generators registered: exactly one** — `HALIDE_REGISTER_GENERATOR(Resnet50Generator, resnet50)` (Resnet50Generator.cpp:383). So A1's *count* is satisfied. But everything about how it is built and benchmarked differs.

**Custom driver instead of RunGenMain.** `process.cpp` has its own `int main` (process.cpp:98-299). The Makefile builds it directly against the emitted `resnet50.a`/`resnet50.h` (Makefile:22-24) — it never compiles or links `RunGenMain.cpp`. This is the "custom `process.cpp`" the task refers to.

**Real weights + real input, loaded from files.**
- `process.cpp` takes CLI args `iterations weight_dir seed output_file` (process.cpp:98-106).
- It loads ~hundreds of weight/shape files from disk via `load_conv_params` / `load_batch_norm_params` / `load_fc_weight` / `load_fc_bias` (process.cpp:45-96, 142-242), reading raw `.data` + `_shape.data` files.
- Those files are produced by `load_weights.py` (Makefile:9-13), which downloads a **pretrained torchvision ResNet-50** (`resnet.resnet50(pretrained=True)`, load_weights.py:14) and dumps each tensor transposed to `dir/<name>.data` + `<name>_shape.data` (load_weights.py:31-40). This needs Python + torchvision + network/model download.
- The input image is random but seeded deterministically: `std::mt19937 e2(seed)` filling a `3×224×224` buffer (process.cpp:108, 244-247).

**Huge, structured input signature (array inputs).** The generator declares **arrays** of Inputs: `Input<Buffer<float,1>[16]>` and `[4]` for gamma/beta/mu/sig, `[16]`/`[4]` conv-weight arrays, plus scalars (Resnet50Generator.cpp:31-66). `process.cpp` passes ~90 buffers to `resnet50(...)`, hand-unrolled with `unroll_array_of_16_buffers` / `unroll_array_of_4_buffers` macros (process.cpp:22-43, 249-279). The AOT call signature is enormous and order-sensitive.

**Benchmarking mechanism is `Halide::Tools::benchmark`, not RunGenMain.** process.cpp:249-279 wraps the `resnet50(...)` call in `benchmark(iterations, 1, lambda)` (halide_benchmark.h, process.cpp:1) and prints `Execution time : %gms`. There is **no** profiler-JSON output, no `HL_PROFILER_JSON_OUTPUT`, no `--benchmarks=all`. Iterations come from `argv[1]` (Makefile passes `10`, Makefile:27).

**Output validation exists and is external.** process.cpp writes the 1000-class output vector to a file (process.cpp:297-298) and prints the argmax class (process.cpp:287-295); `make benchmark_and_validate` then runs `validate_resnet50_output.py` on it (Makefile:26-28). This is a real correctness check the harness has no concept of.

**No estimates on the generator.** `grep` confirms **no `set_estimate(s)`/`estimate()`** anywhere in Resnet50Generator.cpp. Inputs/outputs are unconstrained; the generator even hard-codes `input_shape = {3,224,224}` in the algorithm (Resnet50Generator.cpp:161) rather than via estimates.

**Scheduling is baked into `generate()`.** The schedule (`compute_root`, `vectorize(c,8)`, `parallel(j)`) is inline at Resnet50Generator.cpp:220-232 with a `// TODO: Actually schedule this`. There are no generator parameters — nothing exposed as `GeneratorParam` to sweep.

## Part 3 — Gap analysis (assumption → violation → effort)

**G1. Custom driver (A3) — the core gap. LARGE.**
The harness hard-wires `RunGenMain.cpp` as the driver (build.py:56, 187, 233-236) and hard-wires the run command `--benchmarks=all --estimate_all` with profiler env vars (build.py:248, 243-247). resnet uses its own `process.cpp` main that has an incompatible CLI (`iterations weight_dir seed output_file`, process.cpp:98-106) and links directly against `resnet50.a` (Makefile:22-24). To support it, the harness would need a notion of a per-app **custom driver source** + **custom link recipe** + **custom run command**, replacing three currently-fixed things (`_RUNGENMAIN_*`, `_link`, `_run_benchmark`). These are marked as monkeypatch seams (build.py:22-27) but that is only for tests, not a real app-config abstraction. Large because it touches the whole phase-2/phase-4/run pipeline and the data model (a schedule node currently stores only `generator.cpp` + `generator_parameters.json`, build.py:265-284).

**G2. Benchmark mechanism / profiler JSON (A4, A6). LARGE.**
The harness's entire benchmark object is built from the profiler JSON's single pipeline (`_build_benchmark_obj`, build.py:633-654; one-pipeline assertion build.py:637-641) plus the secret `HL_PROFILER_JSON_TEMPORARY_WARNINGS` file (build.py:247). `process.cpp` produces neither — it prints a human string `Execution time : %gms` (process.cpp:284) via `Halide::Tools::benchmark`. Either (a) resnet's driver would have to be rewritten/adapted to emit the profiler JSON the harness parses, or (b) the harness would need a second ingestion path that scrapes a timing number from stdout. Both are substantial; the benchmark object schema, warnings, and result-state logic all assume the profiler path.

**G3. Input data / weights loading (A4). LARGE (mostly external/environmental).**
The harness benchmarks with random `--estimate_all` inputs and explicitly assumes no file I/O (reference_build_commands.md:20-25). resnet requires ~hundreds of weight files generated by `load_weights.py` (needs torchvision + a pretrained-model download; load_weights.py:14) staged into a `weight_dir` (process.cpp:142-242, Makefile:9-13, 27). The harness has no concept of an input-preparation step, a data directory, or passing a `weight_dir`/`seed` through to the binary. impl.md:602-609 lists "explicit inputs" and "alternative RNG" as future work — confirming this is out of current scope.

**G4. Missing estimates → `--estimate_all` can't size inputs (A4). MEDIUM (if one insisted on the RunGenMain path).**
Even setting weights aside, the generator declares no estimates (confirmed by grep), so RunGenMain `--estimate_all` (build.py:248) has nothing to size the ~90 array inputs/output with. Using the stock harness path would first require adding `set_estimates` to every Input/Output in Resnet50Generator.cpp. Medium and it means editing the app. (This gap disappears if instead we go the custom-driver route of G1, which is why the real answer is the custom driver, not estimates.)

**G5. Array/structured inputs & giant call signature (A1/A4). MEDIUM–LARGE.**
The `Input<Buffer[16]>`/`[4]` arrays (Resnet50Generator.cpp:34-62) and the ~90-argument AOT call (process.cpp:249-279) are exactly why a hand-written `process.cpp` exists: RunGenMain can enumerate inputs generically, but wiring the *correct* named weight file to each array element is app-specific knowledge encoded in process.cpp:163-242. Any harness support inevitably delegates this to a custom driver (folds into G1).

**G6. Output validation (A8). MEDIUM.**
The harness's result state is build/run success only (`_compute_result`, build.py:657-668); there is no correctness notion. resnet has a real validation stage (`validate_resnet50_output.py`, Makefile:26-28) reading the dumped output (process.cpp:297-298). Supporting it means adding an optional post-run validation hook and a new result/aspect in the catalog — a self-contained but genuinely new feature.

**G7. Generator parameters (A7). SMALL — but a modeling mismatch.**
The harness *requires* ≥1 `generator_parameters` object and builds one binary per object (build.py:398-401, 461). resnet exposes **no** `GeneratorParam`s (nothing to sweep; scheduling is inline, Resnet50Generator.cpp:220-232). You could satisfy the harness trivially with a single empty-params object, so the mechanical requirement is small — but it highlights that resnet isn't parameterized for the harness's sweep model.

**G8. Single-generator count (A1). NONE.** resnet registers exactly one generator (Resnet50Generator.cpp:383), so `_discover_generator_name` (build.py:196-214) would be happy. Not a gap.

**G9. `.stmt`/`conceptual_stmt` emit, `host-profile`, Halide layout (A2/A5/A9). SMALL/NONE.** These are compatible: resnet is a standard `Halide::Generator`, so phase-1a compile and `_emit`'s stmt emits work unchanged. `target=host-profile` (build.py:228) vs resnet's Makefile `target=$*` (Makefile:20) differ only in that the harness always adds the profiler feature — fine for a CPU target.

## Bottom line

The count-of-generators assumption (the one the harness enforces most loudly) is **not** the problem — resnet registers exactly one (Resnet50Generator.cpp:383). The real, hard gaps are all downstream of resnet's **custom `process.cpp` driver**:

1. It replaces `RunGenMain` with its own `main` and link recipe (G1).
2. It benchmarks with `Halide::Tools::benchmark` and prints a timing string instead of producing the profiler JSON the harness's entire benchmark-object/warnings/result pipeline is built on (G2, build.py:239-254, 633-654).
3. It needs real pretrained weights + a seeded input staged from files, which the harness explicitly assumes away (G3, reference_build_commands.md:20-25, impl.md:602).

Supporting resnet unchanged is a **large** effort: it requires generalizing the harness from "one fixed RunGenMain driver + profiler-JSON benchmarking of random inputs" to "app-supplied driver source + app-supplied run/benchmark command + input-prep step + timing ingestion," plus optional output validation (G6). The harness's own docs already flag most of these as FUTURE work (impl.md:598-612). The lighter-but-invasive alternative — adapt resnet *to* the harness (add estimates per G4, drop the custom driver, use RunGenMain with random inputs) — would benchmark a functionally meaningless pipeline and throw away resnet's validation, so it defeats the point.

Effort summary: G1 Large · G2 Large · G3 Large · G4 Medium · G5 Medium–Large · G6 Medium · G7 Small · G8/G9 None.
