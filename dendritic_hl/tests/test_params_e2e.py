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

from conftest import (make_catalog_session, Sess, branch_fresh_idea,
                      HALIDE_BUILD_DIR)

pytestmark = [
    pytest.mark.halide,
    pytest.mark.skipif(not os.path.isdir(HALIDE_BUILD_DIR),
                       reason="no local Halide build at " + HALIDE_BUILD_DIR),
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


def _set_params(S, text):
    """Write the workspace generator_parameters.json (a JSON *list*)."""
    S.write_params(text)


def _init_and_build(run_cli, S, profile=1):
    """init_build the (perturbed) workspace as target-only, then build.  First
    branch a fresh, canonical-less idea (the seed idea has a canonical, so
    init_build --target workspace would otherwise refuse to add a child under it
    -- idea.md "Init-Build Tool").  branch_fresh_idea reads the catalog, not the
    workspace, so it works even after the params have been perturbed."""
    branch_fresh_idea(S)
    r = run_cli("init_build", *_cli(S), "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    args = ["build", *_cli(S)]
    if profile:
        args += ["--profile", str(profile)]
    r = run_cli(*args)
    assert r.returncode == 0, r.stderr


# ---- generator parameters now live in the schedule node -------------------

def test_default_parameters(run_cli, tiled_session):
    """The seed workspace defaults to `[{}]` => one profile, empty parameters."""
    _init_and_build(run_cli, tiled_session)
    assert _recorded_params(run_cli, tiled_session) == [{}]


def test_single_parameters_object(run_cli, tiled_session):
    """A one-element list => one profile with those params."""
    _set_params(tiled_session, '[{"split_factor": 16}]')
    _init_and_build(run_cli, tiled_session)
    assert _recorded_params(run_cli, tiled_session) == [{"split_factor": 16}]


def test_parameters_list(run_cli, tiled_session):
    """A multi-element list => one profile per element (order-independent, since
    profiling interleaves the binaries in a shuffled order each batch)."""
    _set_params(tiled_session,
                '[{"split_factor": 8}, {"split_factor": 16}, {"split_factor": 32}]')
    _init_and_build(run_cli, tiled_session)
    recorded = _recorded_params(run_cli, tiled_session)
    assert sorted(p["split_factor"] for p in recorded) == [8, 16, 32]


def test_view_generator_parameters(run_cli, tiled_session):
    """view_generator_parameters prints one line per params object."""
    _set_params(tiled_session, '[{"split_factor": 8}, {"split_factor": 16}]')
    branch_fresh_idea(tiled_session)  # canonical-less idea so init_build adds a child
    r = run_cli("init_build", *_cli(tiled_session), "--other", "none",
                "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("view_generator_parameters", *_cli(tiled_session))
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0].startswith("0 ") and '"split_factor": 8' in lines[0]
    assert lines[1].startswith("1 ") and '"split_factor": 16' in lines[1]


# ---- build: parameter leaves a signature in the emitted .stmt -------------

def _stmt_text(run_cli, S, tmp_path, pidx=0):
    """Fetch the target node's stmt for params index *pidx* via copy_build_output."""
    dst = str(tmp_path / "out.stmt")
    r = run_cli("copy_build_output", *_cli(S), dst, "stmt", "--parameters",
                str(pidx))
    assert r.returncode == 0, r.stderr
    return open(dst, encoding="utf-8").read()


def test_build_parameter_changes_stmt_loop_bound(run_cli, tiled_session,
                                                 tmp_path):
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
    _init_and_build(run_cli, tiled_session, profile=0)
    text = _stmt_text(run_cli, tiled_session, tmp_path)
    assert "x.xi, 0, 7)" in text
    assert "x.xi, 0, 15)" not in text

    # split_factor=16: the inner loop bound tracks the parameter (now 15).
    _set_params(tiled_session, '[{"split_factor": 16}]')
    _init_and_build(run_cli, tiled_session, profile=0)
    text = _stmt_text(run_cli, tiled_session, tmp_path)
    assert "x.xi, 0, 15)" in text
    assert "x.xi, 0, 7)" not in text

    # split_factor=32: bound becomes 31.
    _set_params(tiled_session, '[{"split_factor": 32}]')
    _init_and_build(run_cli, tiled_session, profile=0)
    text = _stmt_text(run_cli, tiled_session, tmp_path)
    assert "x.xi, 0, 31)" in text
