"""End-to-end Path B (shared library + external dlopen runner) against the real
local Halide build.

Proves the shared-library half of the build works: `build` emits a `no_runtime`
`dh_hl_pipeline.{so,dylib}` exporting the stable `dh_hl_pipeline` symbol, and a
runner that OWNS the single Halide runtime can `dlopen` it, resolve the symbol
across the boundary, and run the pipeline (reference_build_commands.md "Path B").

Opt-in (marked `halide`): needs the local ~/Halide build + ninja + a C++
compiler.  Everything goes through the real `./dh_hl` subprocess plus one
hand-compiled runner.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

from dendritic_hl_lib import build
from conftest import _PKG_ROOT

pytestmark = [
    pytest.mark.halide,
    pytest.mark.skipif(not os.path.isdir(build.HALIDE_BUILD),
                       reason="no local Halide build at " + build.HALIDE_BUILD),
    pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not found"),
]

_BRIGHTEN = os.path.join(_PKG_ROOT, "rungen_example", "brighten_generator.cpp")

# A minimal dlopen runner (reference_build_commands.md "Preparing a runner"):
# include the generated header for the prototype, marshal buffers with
# HalideBuffer.h, dlopen the no_runtime .so, resolve the STABLE `dh_hl_pipeline`
# symbol, and call it.  The runner owns the runtime (linked below), so the .so's
# undefined halide_* bind upward to it at load time.
_RUNNER_SRC = r"""
#include "dh_hl_pipeline.h"
#include "HalideBuffer.h"
#include <dlfcn.h>
#include <cstdio>

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: runner <lib>\n"); return 2; }
    void *h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!h) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 3; }
    auto fn = reinterpret_cast<decltype(&dh_hl_pipeline)>(
        dlsym(h, "dh_hl_pipeline"));
    if (!fn) { fprintf(stderr, "dlsym: %s\n", dlerror()); return 4; }
    Halide::Runtime::Buffer<uint8_t> in(64, 64), out(64, 64);
    in.fill(100);
    int rc = fn(in, out);
    // offset defaults to 10, so 100 -> 110.
    printf("rc=%d out=%d\n", rc, (int)out(0, 0));
    return rc;
}
"""


# A PROFILING runner for the <Lib> problem end-to-end test.  It proves three
# things a `<Lib>` problem relies on: the shared library reaches the runner via
# the DENDRITIC_HL_OUTPUT_LIB env var (NOT read from argv), the <Lib> argv
# substitution equals that env path, and a non-<Lib> argv token is passed through
# verbatim.  It then benchmarks the pipeline so the runtime emits the profiler
# JSON (HL_PROFILER_JSON_OUTPUT), exactly as RunGenMain would.
_PROFILING_RUNNER_SRC = r"""
#include "dh_hl_pipeline.h"
#include "HalideBuffer.h"
#include "HalideRuntime.h"
#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

int main(int argc, char **argv) {
    const char *env_lib = getenv("DENDRITIC_HL_OUTPUT_LIB");
    if (!env_lib) { fprintf(stderr, "no DENDRITIC_HL_OUTPUT_LIB\n"); return 2; }
    // argv[1] is the <Lib> substitution; it must equal the env var path.
    if (argc < 3 || strcmp(argv[1], env_lib) != 0) {
        fprintf(stderr, "lib arg != env\n"); return 3; }
    // argv[2] is a non-<Lib> token the problem specified, passed through as-is.
    if (strcmp(argv[2], "--marker=MAGIC") != 0) {
        fprintf(stderr, "marker missing\n"); return 4; }
    void *h = dlopen(env_lib, RTLD_NOW | RTLD_LOCAL);
    if (!h) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 5; }
    auto fn = reinterpret_cast<decltype(&dh_hl_pipeline)>(
        dlsym(h, "dh_hl_pipeline"));
    if (!fn) { fprintf(stderr, "dlsym: %s\n", dlerror()); return 6; }
    Halide::Runtime::Buffer<uint8_t> in(256, 256), out(256, 256);
    in.fill(100);
    for (int i = 0; i < 200; i++) fn(in, out);
    halide_profiler_report(nullptr);   // flush; the runtime emits the JSON at exit
    return 0;
}
"""


def _line(out, prefix):
    for ln in out.splitlines():
        if ln.startswith(prefix):
            return ln[len(prefix):].strip()
    raise AssertionError("no line starting {!r} in:\n{}".format(prefix, out))


def test_shared_library_dlopen_runner(run_cli, tmp_path):
    # 1. Bootstrap a catalog with the brighten generator and build it (default
    #    params -> a single (node, 0) subdir with the .so + halide_runtime.o).
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "p.txt").write_text("shared lib\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed", str(tmp_path / "p.txt"),
                _BRIGHTEN)
    assert r.returncode == 0, r.stderr
    handle = _line(r.stdout, "Session handle: ")
    assert run_cli("init_workspace", "-s", handle).returncode == 0
    r = run_cli("init_build", "-s", handle, "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--only", "all")  # no profiling needed
    assert r.returncode == 0, r.stderr

    # 2. Fetch the artifacts a runner needs through the real CLI getter
    #    (copy_build_output) -- a runner is only supposed to need these outputs,
    #    not knowledge of the bin/ layout.  The shared library and generated
    #    header come out this way; the standalone runtime object has no getter, so
    #    it is still taken from bin/ (IMPL TASK: add a getter if one is wanted).
    def _fetch(what, name):
        dst = str(tmp_path / name)
        r = run_cli("copy_build_output", "-s", handle, dst, what)
        assert r.returncode == 0, r.stderr
        return dst

    lib = _fetch("shared_library", build._shared_lib_filename())
    _fetch("header", "dh_hl_pipeline.h")     # runner #includes this
    bin_dir = run_cli("workspace_bin", "-s", handle).stdout.strip()
    runtime_obj = os.path.join(bin_dir, build._RUNTIME_OBJ)
    assert os.path.isfile(lib) and os.path.isfile(runtime_obj)

    # 3. Compile the runner: it OWNS the runtime (links halide_runtime.o) and
    #    exports its symbols so the .so's undefined halide_* resolve upward
    #    (-export_dynamic on macOS / -rdynamic on Linux).
    runner_src = tmp_path / "runner.cpp"
    runner_src.write_text(_RUNNER_SRC)
    runner_bin = str(tmp_path / "runner")
    export_flag = ("-Wl,-export_dynamic" if sys.platform == "darwin"
                   else "-rdynamic")
    compile_cmd = [
        "c++", "-std=c++17", "-O2",
        "-I" + os.path.join(build.HALIDE_BUILD, "include"),
        "-I" + os.path.join(build.HALIDE_ROOT, "src", "runtime"),
        "-I" + str(tmp_path),                # dh_hl_pipeline.h (fetched above)
        str(runner_src), runtime_obj,
        "-o", runner_bin, export_flag, "-lpthread", "-ldl"]
    cp = subprocess.run(compile_cmd, capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr

    # 4. Run the runner against the .so: it dlopens, resolves the stable symbol
    #    across the boundary, and computes 100 + 10 = 110.
    rp = subprocess.run([runner_bin, lib], capture_output=True, text=True)
    assert rp.returncode == 0, rp.stderr
    assert "rc=0 out=110" in rp.stdout, rp.stdout


def test_lib_problem_profiled_end_to_end(run_cli, tmp_path):
    """A `<Lib>` (custom-runner) problem profiled through `dh_hl build --profile`:
    the harness builds the no_runtime .so, sets DENDRITIC_HL_OUTPUT_LIB, and runs
    the prebuilt runner, which benchmarks via dlopen.  Proves the whole non-
    RunGenMain profile path -- including that the env var reaches the runner and
    that non-<Lib> argv tokens pass through -- and records a real benchmark."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "p.txt").write_text("lib problem\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed", str(tmp_path / "p.txt"),
                _BRIGHTEN)
    assert r.returncode == 0, r.stderr
    handle = _line(r.stdout, "Session handle: ")
    assert run_cli("init_workspace", "-s", handle).returncode == 0

    # Build once to produce the .so + the shared runtime object + header.
    assert run_cli("init_build", "-s", handle, "--other", "none",
                   "--anchor", "none").returncode == 0
    assert run_cli("build", "-s", handle, "--only", "all").returncode == 0
    _fetch = lambda what, name: (
        run_cli("copy_build_output", "-s", handle, str(tmp_path / name), what))
    assert _fetch("header", "dh_hl_pipeline.h").returncode == 0
    bin_dir = run_cli("workspace_bin", "-s", handle).stdout.strip()
    runtime_obj = os.path.join(bin_dir, build._RUNTIME_OBJ)

    # Build the profiling runner (owns the runtime, exports symbols).
    (tmp_path / "runner.cpp").write_text(_PROFILING_RUNNER_SRC)
    runner_bin = str(tmp_path / "runner")
    export_flag = ("-Wl,-export_dynamic" if sys.platform == "darwin"
                   else "-rdynamic")
    cp = subprocess.run(
        ["c++", "-std=c++17", "-O2",
         "-I" + os.path.join(build.HALIDE_BUILD, "include"),
         "-I" + os.path.join(build.HALIDE_ROOT, "src", "runtime"),
         "-I" + str(tmp_path),
         str(tmp_path / "runner.cpp"), runtime_obj,
         "-o", runner_bin, export_flag, "-lpthread", "-ldl"],
        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr

    # A <Lib> problem: argv[0] the runner, then <Lib> (substituted to the .so
    # path + exported as DENDRITIC_HL_OUTPUT_LIB) and a passthrough token.
    r = run_cli("new_problem", "-s", handle, "librun",
                runner_bin, "<Lib>", "--marker=MAGIC")
    assert r.returncode == 0, r.stderr
    # Profile with ONLY the lib problem.
    assert run_cli("disable_problem", "-s", handle,
                   "problem.default").returncode == 0
    assert run_cli("init_build", "-s", handle, "--other", "none",
                   "--anchor", "none").returncode == 0
    r = run_cli("build", "-s", handle, "--profile", "2", "--only", "all",
                "--problem", "problem.librun")
    assert r.returncode == 0, r.stderr
    assert "problem problem.librun (success)" in r.stdout
    assert "dh_hl: ... with Benchmark ID:" in r.stdout

    # A real benchmark was recorded for the lib problem -> a queryable cost.
    r = run_cli("json_ranking_cost", "-s", handle, "--anchor", "none",
                "--problem", "problem.librun")
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["batch_count"] == 2 and obj["cost"] is not None


# A deliberately broken runner: exits 0 but emits no profiler JSON at all (it
# never loads the library or sets up the profiler).  Same observable contract as
# the idea.md "_exit(0) before the profiler teardown" case.
_BROKEN_RUNNER_SRC = "int main() { return 0; }\n"


def test_broken_runner_no_json_is_catalogued_bad_outcome(run_cli, tmp_path):
    """A <Lib> problem whose runner exits 0 but emits NO profiler JSON is a
    catalogued bad outcome, not a harness crash (idea.md Build Tool): the profile
    loop skips that benchmark and keeps going -- the run banner is (success) (the
    process exited 0), a 'no profiler JSON' notice goes to stderr, the build exits
    nonzero, NO benchmark set is made, and the node still ends at `success` (the
    generators built) with zero benchmarks."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "p.txt").write_text("broken runner\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed", str(tmp_path / "p.txt"),
                _BRIGHTEN)
    assert r.returncode == 0, r.stderr
    handle = _line(r.stdout, "Session handle: ")
    assert run_cli("init_workspace", "-s", handle).returncode == 0

    (tmp_path / "broken.cpp").write_text(_BROKEN_RUNNER_SRC)
    broken = str(tmp_path / "broken")
    cp = subprocess.run(["c++", "-O2", str(tmp_path / "broken.cpp"), "-o", broken],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr

    r = run_cli("new_problem", "-s", handle, "broken", broken, "<Lib>")
    assert r.returncode == 0, r.stderr
    assert run_cli("disable_problem", "-s", handle,
                   "problem.default").returncode == 0
    assert run_cli("init_build", "-s", handle, "--other", "none",
                   "--anchor", "none").returncode == 0
    r = run_cli("build", "-s", handle, "--profile", "1", "--only", "all",
                "--problem", "problem.broken")
    # Bad outcome -> nonzero exit, but a clean one (no Python traceback).
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "problem problem.broken (success)" in r.stdout   # the process exited 0
    assert "no profiler JSON" in r.stderr                    # skip notice, not crash
    assert "Benchmark set ID:" not in r.stdout               # nothing profiled

    # The node persisted: success result, and zero benchmarks recorded.
    r = run_cli("json_schedule_info", "-s", handle)
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["result"] == "success" and obj["benchmark"] == []
