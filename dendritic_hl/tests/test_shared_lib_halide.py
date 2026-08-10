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

    # 2. Locate the emitted subdir (holds the .so, header) + the shared runtime.
    # IMPL TASK: once `copy_build_output` exists (idea.md "Copy Build Output
    # Tool"), fetch the shared library + generated header through it (real CLI
    # getters) instead of reaching into the bin/ layout directly here; a runner is
    # only supposed to need copy_build_output outputs, so this test should model
    # that.  (halide_runtime.o has no getter yet -- flag if one is wanted.)
    bin_dir = run_cli("workspace_bin", "-s", handle).stdout.strip()
    subdirs = [d for d in os.listdir(bin_dir)
               if d.endswith("_0") and os.path.isdir(os.path.join(bin_dir, d))]
    assert len(subdirs) == 1, subdirs
    subdir = os.path.join(bin_dir, subdirs[0])
    lib = os.path.join(subdir, build._shared_lib_filename())
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
        "-I" + subdir,                       # dh_hl_pipeline.h
        str(runner_src), runtime_obj,
        "-o", runner_bin, export_flag, "-lpthread", "-ldl"]
    cp = subprocess.run(compile_cmd, capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr

    # 4. Run the runner against the .so: it dlopens, resolves the stable symbol
    #    across the boundary, and computes 100 + 10 = 110.
    rp = subprocess.run([runner_bin, lib], capture_output=True, text=True)
    assert rp.returncode == 0, rp.stderr
    assert "rc=0 out=110" in rp.stdout, rp.stdout
