"""End-to-end generator-parameter tests driving the real `dh_hl` CLI.

Opt-in (marked `halide`): needs the local ~/Halide build + ninja, like
test_halide.py.  Uses a tiny generator with a `split_factor` GeneratorParam so
that a parameter both (a) is recorded verbatim in each profile benchmark and
(b) leaves a visible signature in the emitted `.stmt` (the inner split loop's
constant bound).
"""

import json
import os
import shutil

import pytest

from dendritic_hl_lib import build
from conftest import make_catalog_session, Sess

pytestmark = [
    pytest.mark.halide,
    pytest.mark.skipif(not os.path.isdir(build.HALIDE_BUILD),
                       reason="no local Halide build at " + build.HALIDE_BUILD),
    pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not found"),
]

# split(x, xo, xi, split_factor): the inner loop `xi` gets a constant bound
# equal to split_factor.  This build's .stmt prints loops as
# `for (var, min, max_inclusive)`, so split_factor F shows as `x.xi, 0, F-1)`.
TILED_SOURCE = """\
#include "Halide.h"
using namespace Halide;
namespace {
class Tiled : public Generator<Tiled> {
public:
    GeneratorParam<int> split_factor{"split_factor", 8};
    Input<Buffer<uint8_t, 1>>  input{"input"};
    Output<Buffer<uint8_t, 1>> output{"output"};
    Var x{"x"};
    void generate() { output(x) = cast<uint8_t>(input(x) + 1); }
    void schedule() {
        input.set_estimates({{0, 1024}});
        output.set_estimates({{0, 1024}});
        Var xo{"xo"}, xi{"xi"};
        output.split(x, xo, xi, split_factor);
    }
};
}
HALIDE_REGISTER_GENERATOR(Tiled, tiled)
"""


@pytest.fixture
def tiled_session(tmp_path):
    """A catalog+session whose consistent workspace holds TILED_SOURCE, so
    build/profile edit the seed idea's canonical schedule directly."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    catalog_dir, session_id = make_catalog_session(cat_dir, source=TILED_SOURCE)
    return Sess(catalog_dir, session_id)


def _cli(S):
    return ["-s", S.session_id, "-C", S.catalog_dir]


def _schedule_json(run_cli, S):
    r = run_cli("json_schedule_info", *_cli(S))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _recorded_params(run_cli, S):
    return [b["parameters"] for b in _schedule_json(run_cli, S)["benchmark"]]


# ---- profile: the three parameters-file shapes ----------------------------

def test_profile_no_parameters_file(run_cli, tiled_session):
    """Omitted parameters file => a single profile with empty parameters."""
    assert run_cli("profile", *_cli(tiled_session)).returncode == 0
    assert _recorded_params(run_cli, tiled_session) == [{}]


def test_profile_single_object_file(run_cli, tiled_session, tmp_path):
    """A file holding a single JSON object => one profile with those params."""
    pf = tmp_path / "p.json"
    pf.write_text('{"split_factor": 16}')
    assert run_cli("profile", *_cli(tiled_session), str(pf)).returncode == 0
    assert _recorded_params(run_cli, tiled_session) == [{"split_factor": 16}]


def test_profile_list_file(run_cli, tiled_session, tmp_path):
    """A file holding a JSON list => one profile per element, in order."""
    pf = tmp_path / "p.json"
    pf.write_text('[{"split_factor": 8}, {"split_factor": 16}, {"split_factor": 32}]')
    assert run_cli("profile", *_cli(tiled_session), str(pf)).returncode == 0
    assert _recorded_params(run_cli, tiled_session) == [
        {"split_factor": 8}, {"split_factor": 16}, {"split_factor": 32}]


def test_profile_object_via_stdin(run_cli, tiled_session):
    """`-` reads the parameters JSON from stdin (the universal stdin path)."""
    r = run_cli("profile", *_cli(tiled_session), "-", input='{"split_factor": 32}')
    assert r.returncode == 0, r.stderr
    assert _recorded_params(run_cli, tiled_session) == [{"split_factor": 32}]


# ---- build: parameter leaves a signature in the emitted .stmt -------------

def test_build_parameter_changes_stmt_loop_bound(run_cli, tiled_session, tmp_path):
    stmt = os.path.join(tiled_session.private_dir, "bin", "dh_hl_gen.stmt")

    # NOTE: the `x.xi, 0, N)` fragments below encode what I observed of this
    # Halide build's .stmt syntax (empirically, not from a spec):
    #   * a for-loop prints as `for (<var>, <min>, <max>)` with an INCLUSIVE
    #     max -- not the `(min, extent)` form Halide's mainline IRPrinter uses.
    #     So `output.split(x, xo, xi, F)` yields inner loop `x.xi, 0, F-1)`
    #     (F=8 -> 7, 16 -> 15, 32 -> 31), the constant we key on.
    #   * the split var keeps its given name `xi`, fully qualified as
    #     `output.s0.x.xi` (stage.dim.var); matching the `x.xi` tail is enough.
    # If a Halide upgrade changes either convention, update these fragments.

    # Default GeneratorParam (split_factor=8): inner split loop bound is 7.
    assert run_cli("build", *_cli(tiled_session)).returncode == 0
    text = open(stmt).read()
    assert "x.xi, 0, 7)" in text
    assert "x.xi, 0, 15)" not in text

    # split_factor=16: the inner loop bound tracks the parameter (now 15).
    pf16 = tmp_path / "p16.json"
    pf16.write_text('{"split_factor": 16}')
    assert run_cli("build", *_cli(tiled_session), str(pf16)).returncode == 0
    text = open(stmt).read()
    assert "x.xi, 0, 15)" in text
    assert "x.xi, 0, 7)" not in text

    # split_factor=32 via stdin: bound becomes 31.
    r = run_cli("build", *_cli(tiled_session), "-", input='{"split_factor": 32}')
    assert r.returncode == 0, r.stderr
    text = open(stmt).read()
    assert "x.xi, 0, 31)" in text
