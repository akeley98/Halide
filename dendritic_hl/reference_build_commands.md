# Reference Build Commands

The following was tested end-to-end against the local Halide build at
`~/Halide/build/` and produces a standalone binary that both benchmarks
(via the profiler) and emits the `.stmt` and `conceptual.stmt`. In these examples the
name `brighten` is used for both `-g` (generator name) and `-f` (output
basename) since the example generator registers `brighten`. The real tool
instead **discovers** the `-g` name (run the generator exe with no `-g` and
scrape the `available Generators are:` list, which holds a single name under
the single-generator assumption) and passes a **fixed** `-f` basename such as
`dh_hl_gen`; this decoupled variant was also tested end-to-end.

**Gotchas that cost time (all confirmed on David's MacBook):**

* `HalideBuffer.h` and `HalideRuntime.h` are **not** in `build/include/`
  (which only has `Halide.h`); they live in `~/Halide/src/runtime/`,
  so `RunGenMain` must be compiled with `-I ~/Halide/src/runtime`.
* The `conceptual_stmt` emit produces a file with extension **`.conceptual.stmt`**,
  not `.conceptual_stmt`. (The plain `stmt` emit produces `.stmt`.)
* Compile `RunGenMain` with `-fno-exceptions -DHALIDE_NO_PNG -DHALIDE_NO_JPEG`
  so it doesn't drag in libpng/libjpeg; benchmarking uses random/estimated
  inputs, so no image I/O is needed.
* The `static_library` emit already embeds the Halide runtime, so no separate
  `runtime.a` needs linking (unlike Halide's own root `Makefile`, which emits
  with `no_runtime`). Only `-lpthread -ldl` are needed at link time.

## Path A — RunGenMain, static link (the current dh_hl build tool)

Phase 1 — build the generator executable (the `GenGen` main lives inside
`libHalide_GenGen.a`):

    c++ -std=c++17 -O2 -I$H/include -I$H/../tools \
        generator.cpp -o generator_exe \
        $H/tools/libHalide_GenGen.a -L$H/src -lHalide -Wl,-rpath,$H/src

Phase 2 — run the generator; append generator params as trailing `key=value`
tokens (formatted per the `%d`/`%r` rule above):

    ./generator_exe -g brighten -o . -f brighten [key=value ...] \
        -e static_library,c_header,registration,stmt,conceptual_stmt \
        target=host-profile

Phase 3 — compile `RunGenMain` (note the `src/runtime` include):

    c++ -c -std=c++17 -O2 -fno-exceptions -DHALIDE_NO_PNG -DHALIDE_NO_JPEG \
        -I$H/include -I$H/../src/runtime -I$H/../tools -I. \
        $H/../tools/RunGenMain.cpp -o RunGenMain.o

Phase 4 — link the standalone binary:

    c++ -std=c++17 -O2 RunGenMain.o brighten.registration.cpp brighten.a \
        -o brighten.rungen -lpthread -ldl

Run / benchmark:

    HL_PROFILER_JSON_OUTPUT=out.json ./brighten.rungen --benchmarks=all --estimate_all

where `$H` = `~/Halide/build`. For `profile`, phase 1 runs once and phases
2--4 + the run loop over each parameter set. Only phase 2 sees the generator
params, so the loop must re-emit and re-link per parameter set.

A worked, tested generator + ninja build is under
`dendritic_hl/rungen_example/`. Run it with

    ninja -f build_ninja.txt brighten.rungen

The ninja file is named `build_ninja.txt` (not `build.ninja`) so it escapes
this repo's `*.ninja*` gitignore rule and can be committed; hence the `-f`.


### Temporary-ish: Warnings Output

Andrew Adams didn't include the warnings in the JSON output.
For now, to make forward progress, there's a separate secret menu
`HL_PROFILER_JSON_TEMPORARY_WARNINGS` environment variable.

This names a file in which per-pipeline warning information gets written,
in "JSON lines" format.
Each line is a JSON object, containing keys `pipeline` and `warnings`.

The warnings are a list of objects, each object containing at least:

* `rule`: string "slug" identifier of warning kind

* `func`: string name of func

* `message`: string

* `canonical_id`: numeric unique ID of func in this pipeline

For now, since the harness requires only 1 generator per file,
parse it as if it were a single JSON object.
Copy the inner "warnings" list to the corresponding
`warnings` key/value pair in the `dh_hl` benchmark JSON format.

The function names may collide for different functions in the same
pipeline. This makes `WarningToggle` incapable of distinguishing
them, which is ignored for the prototype.
We do have `canonical_id` but that's useless for giving a
stable identifier after scheduling changes.


## Path B — shared object + external runner (dlopen)

An alternative to Path A that dh_hl plans to support **alongside** it. Instead of
statically linking the emitted `.a` into a stock `RunGenMain` binary, the harness
emits the pipeline as a **shared object** and a **prebuilt runner loads it at
runtime with `dlopen`**. The runner is built once (by the harness user, possibly
via an arbitrarily gnarly legacy build) and never rebuilt in the hot loop; the
harness only ever regenerates the `.so`/`.dylib`. Everything below was tested
end-to-end against `~/Halide/build` on macOS (arm64); Linux notes are a best guess
(Linux is the easier case). `$H` = `~/Halide/build`, `brighten` is the example
generator.

### The Halide runtime caveat (read this first)

A Halide `object`/`static_library` emit embeds a **full copy of the Halide
runtime, using weak linkage**. Weak-symbol overrides are **not reliably honored
across a shared-library boundary** — if the pipeline `.so` carries its own runtime
and the runner tries to install a custom `halide_do_par_for` (e.g. a custom thread
pool), the builtin inside the `.so` may win. Halide's own `HalideRuntime.h` warns
about exactly this ("if the override is in a shared library and the halide object
files are linked directly into the output, the builtin versions ... will be
called ... On Linux, `LD_DYNAMIC_WEAK=1` may help").

So the robust arrangement — the one used below — is:

* Compile the pipeline object with **`no_runtime`**, so the `.so` has *undefined*
  `halide_*` symbols and no runtime of its own.
* Have the **runner own the single runtime** (link one standalone runtime object)
  **plus any custom overrides** (thread pool, error/print handlers). The `.so`'s
  undefined symbols then resolve *upward* to the runner at load time — ordinary
  dynamic linking, not weak interposition — so overrides always apply.

Phase 1 — build the generator executable: identical to Path A.

Phase 2 — emit the pipeline as a **`no_runtime` object** plus the C header (append
`profile` to the target if you intend to profile; add `hlpipe,stmt` etc. as
wanted):

    ./brighten.generator -g brighten -o . -f brighten \
        -e object,c_header target=host-no_runtime
    # for profiling instead: target=host-profile-no_runtime

Phase 3 — emit a **standalone Halide runtime** object for the runner to own (match
the profile feature to Phase 2):

    ./brighten.generator -r halide_runtime -o . -e object target=host
    # for profiling instead: target=host-profile

Phase 4 — link the pipeline object into a shared object:

    # macOS: allow the undefined halide_* to be resolved at load time.
    c++ -shared -o brighten.dylib brighten.o -Wl,-undefined,dynamic_lookup

    # Linux (best guess): -shared already permits undefined symbols, so no special
    # flag is needed; the emitted object is already PIC.
    c++ -shared -o brighten.so brighten.o

Confirm the pipeline entry is exported and only `halide_*` are left undefined
(macOS): `nm -g brighten.dylib | grep _brighten` and `nm -u brighten.dylib`. A
`no_runtime` `.dylib` has no dylib dependencies (`otool -L brighten.dylib`), so
there are no `@rpath`/`install_name` concerns — the runner `dlopen`s an absolute
path.

### Resolving the missing (`halide_*`) symbols at load time

The `.so` deliberately leaves the runtime undefined; the **runner** must export its
own runtime symbols so the loader can bind them when the `.so` is opened:

* **macOS:** link the runner with **`-Wl,-export_dynamic`** (put its global symbols
  in the dynamic symbol table). Build the `.dylib` with `-Wl,-undefined,
  dynamic_lookup` (Phase 4). `dlopen(path, RTLD_NOW | RTLD_LOCAL)` works —
  `RTLD_LOCAL` on the loaded lib is fine because the *executable's* symbols are what
  satisfy it.
* **Linux (best guess):** link the runner with **`-rdynamic`**
  (`-Wl,--export-dynamic`). No special flag on the `.so`. `dlopen(path, RTLD_NOW)`.
  Only if you (against advice) keep an embedded weak runtime in the `.so` and find a
  custom override not taking effect, try `LD_DYNAMIC_WEAK=1`.

Run / benchmark: the runner sets the profiler env var exactly as Path A; the
profiler lives in the runtime the runner owns, so `HL_PROFILER_JSON_OUTPUT`
produces the same JSON (verified: `pipelines[0].name == "brighten"`, the `-f`
basename, flows through the `dlopen` boundary).

    HL_PROFILER_JSON_OUTPUT=out.json ./runner ./brighten.dylib

The exact commands above were run end-to-end during research (generator ->
`no_runtime` object + header, standalone runtime object, `.dylib`, and a minimal
`dlopen` runner) and reproduce a working Path B; see the "Preparing a runner"
section below for the runner body.


## Preparing a runner to use a Halide header + shared library

Basic, one-time steps to make an existing runner load a dh_hl-emitted pipeline via
`dlopen` (per Path B). This is done once per runner (by hand / by an agent), never
in the hot loop.

1. **Include the generated header for the entry declarations.** `#include
   "brighten.h"` provides the `extern "C"` prototypes: `int brighten(halide_buffer_t*
   ..., halide_buffer_t*)`, `int brighten_argv(void**)`, and
   `const halide_filter_metadata_t *brighten_metadata()`. Also `#include
   "HalideBuffer.h"` (header-only, no linking) to marshal inputs/outputs as
   `Halide::Runtime::Buffer<T>` — this is unchanged from the static-link world.

2. **Own the runtime in the runner.** Link one standalone runtime object
   (`halide_runtime.o` from Phase 3, or `libHalideRuntime`) and install any custom
   handlers here (e.g. `halide_set_custom_do_par_for(...)`, error/print handlers).
   Because the pipeline `.so` is `no_runtime`, these apply to whatever pipeline is
   loaded.

3. **Resolve the entry point**, either:
   * *fixed signature* — `void* h = dlopen(path, RTLD_NOW|RTLD_LOCAL);` then
     `auto fn = (int(*)(halide_buffer_t*, ..., halide_buffer_t*))dlsym(h,
     "brighten");` cast to the header's prototype; or
   * *signature-agnostic* — `dlsym` `brighten_argv` + `brighten_metadata`, read the
     `halide_filter_metadata_t` for arg count/types/dims, build a `void* args[]`,
     and call `_argv`. No runner recompile even if the arg list changes.

4. **Call it** with the buffers, exactly as a statically-linked call would. Minimal
   verified body:

        void* h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
        auto fn = (int(*)(halide_buffer_t*, halide_buffer_t*))dlsym(h, "brighten");
        Halide::Runtime::Buffer<uint8_t> in(64, 64), out(64, 64);
        in.fill(100);
        int rc = fn(in, out);   // rc == 0, out(0,0) == 110

5. **Build the runner once** (`-Wl,-export_dynamic` on macOS / `-rdynamic` on
   Linux, include `-I$H/include -I$H/../src/runtime -I.`, link the runtime object,
   `-lpthread -ldl`). Take the `.so` path (and, per node, the parameters index) as
   an argument so the harness swaps pipelines without rebuilding the runner.

6. **To profile:** set `HL_PROFILER_JSON_OUTPUT` and make sure Phases 2--3 used a
   `profile` target; call the pipeline in a loop (or `Halide::Tools::benchmark`),
   optionally `halide_profiler_report(nullptr)`.


## Emitting the algorithm `hlpipe` in the generator

For the algorithm-equality check (a schedule-free fingerprint), the generator
serializes its pipeline **before any scheduling directive runs**. This is a
one-liner, available via `#include "Halide.h"` (no extra include needed):

    serialize_pipeline(Pipeline(std::vector<Func>{output}), path);

* List **all** outputs in the `Pipeline{...}` if there is more than one. `output`
  is the generator's `Output<Buffer>` (converts to `Func`).
* `path` is where to write the `.hlpipe` — e.g. read an env var the harness sets,
  or use a fixed filename in the output dir.

**Placement matters — it must precede scheduling:**

* If the generator uses the `generate()` / `schedule()` split, put the call at the
  **end of `generate()`** (algorithm defined, `schedule()` not yet called).
* If everything is in `generate()` (the common case), insert it **right after the
  algorithm and before the first scheduling directive**.

Do **not** rely on the `-e hlpipe` emit for this: that emit runs *after*
`generate()` (post-schedule), so it would capture the schedule. The in-generator
`serialize_pipeline` call is a snapshot of the current pipeline, so it is
schedule-free by construction.

Verified properties (tested against this Halide build): the blob is
byte-deterministic across runs; byte-identical for the same algorithm regardless of
the schedule code that follows the checkpoint (and regardless of the `-f` name and
target); invariant to incidental C++ (`source_location` is serialized empty, and
renaming a C++ local variable does not change it); and it differs on any real
algorithm change (including a semantically-equivalent commutative reorder). So the
comparison against the per-root golden is a plain byte compare:
`cmp golden.hlpipe candidate.hlpipe`.
