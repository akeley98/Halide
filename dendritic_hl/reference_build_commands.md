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
