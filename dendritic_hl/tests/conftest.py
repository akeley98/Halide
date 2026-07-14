"""Shared pytest fixtures for the dh_hl test suite.

These tests are NOT shipped with the package, so they may use pytest/hypothesis
even though the package itself is stdlib-only.
"""

import os
import subprocess
import sys
import types

import pytest

# Make `import dendritic_hl_lib` work regardless of where pytest is invoked.
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

DH_HL = os.path.join(_PKG_ROOT, "dh_hl")

# A trivial, compilable-looking generator body.  Content only matters to the
# real-Halide tests; everything else just hashes/stores it.
DUMMY_SOURCE = """\
#include "Halide.h"
using namespace Halide;
class Dummy : public Generator<Dummy> {
public:
    Input<Buffer<uint8_t, 2>> input{"input"};
    Output<Buffer<uint8_t, 2>> output{"output"};
    Var x, y;
    void generate() { output(x, y) = input(x, y); }
};
HALIDE_REGISTER_GENERATOR(Dummy, dummy)
"""


@pytest.fixture
def reset_safety():
    """Isolate the safety module's process-global state between tests."""
    from dendritic_hl_lib import safety
    safety._new_entries.clear()
    safety._pending_overwrites.clear()
    safety._new_file_count = 0
    yield safety
    safety._new_entries.clear()
    safety._pending_overwrites.clear()
    safety._new_file_count = 0


@pytest.fixture
def workspace(tmp_path, reset_safety):
    """A workspace .cpp file on disk (no catalog yet)."""
    ws = tmp_path / "gen.cpp"
    ws.write_text(DUMMY_SOURCE)
    return ws


def ns(**kwargs):
    """Build an argparse-style namespace for calling cmd_* functions directly."""
    kwargs.setdefault("schedule", None)
    kwargs.setdefault("parameters", None)
    return types.SimpleNamespace(**kwargs)


@pytest.fixture
def run_cli():
    """Run ./dh_hl as a real subprocess; returns CompletedProcess."""
    def _run(*args, env=None, input=None):
        e = dict(os.environ)
        if env:
            e.update(env)
        return subprocess.run(
            [DH_HL, *args], capture_output=True, text=True, env=e, input=input)
    return _run
