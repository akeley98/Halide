"""Real-Halide, real-CLI coverage that the PROBLEM actually drives the run and
the cost/stats tools reflect it.

These are the tests that would fail if `build` ignored a problem's argv (e.g.
hard-wired `--benchmarks=all --estimate_all` for the RunGenMain path): two
problems that run the SAME schedule at different input sizes must produce
different real costs, and the cost/profiler-stats tools must report the selected
problem's numbers.

Opt-in (marked `halide`): needs the local ~/Halide build + ninja.  Everything
goes through the real `./dh_hl` subprocess.
"""

import json
import os
import shutil

import pytest

from conftest import _PKG_ROOT, HALIDE_DIR, HALIDE_BUILD_DIR

pytestmark = [
    pytest.mark.halide,
    pytest.mark.skipif(not os.path.isdir(HALIDE_BUILD_DIR),
                       reason="no local Halide build at " + HALIDE_BUILD_DIR),
    pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not found"),
]

_BRIGHTEN = os.path.join(_PKG_ROOT, "rungen_example", "brighten_generator.cpp")

# Two RunGenMain problems that run the brighten pipeline at very different input
# sizes (fixed, explicit extents -- NOT the generator's set_estimate size), with
# a short benchmark window to keep the test quick.  The large one does ~256x more
# work, so its real wall_time is unmistakably higher -- unless the harness ignores
# the argv and runs both at the (estimate) default, which is exactly the
# regression these tests guard against.
_SMALL = ["<RunGenMain>", "--benchmarks=all", "--benchmark_min_time=0.02",
          "input=zero:[64,64]", "--output_extents=[64,64]"]
_LARGE = ["<RunGenMain>", "--benchmarks=all", "--benchmark_min_time=0.02",
          "input=zero:[1024,1024]", "--output_extents=[1024,1024]"]


def _line(out, prefix):
    for ln in out.splitlines():
        if ln.startswith(prefix):
            return ln[len(prefix):].strip()
    raise AssertionError("no line starting {!r} in:\n{}".format(prefix, out))


def _bootstrap_two_problems(run_cli, tmp_path):
    """new_catalog + init_workspace + a `small` and `large` RunGenMain problem.
    new_catalog creates no problem, so these two are the only enabled problems
    and `build --profile` (no --problem) profiles exactly them.  Returns the
    session handle."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "p.txt").write_text("problem sizes\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed", str(tmp_path / "p.txt"),
                _BRIGHTEN)
    assert r.returncode == 0, r.stderr
    handle = _line(r.stdout, "Session handle: ")
    assert run_cli("init_workspace", "-s", handle).returncode == 0
    assert run_cli("set_halide_path", "-s", handle,
                   HALIDE_DIR).returncode == 0
    for name, argv in (("small", _SMALL), ("large", _LARGE)):
        r = run_cli("new_problem", "-s", handle, name, *argv)
        assert r.returncode == 0, r.stderr
    return cat_dir, handle


def _profile(run_cli, handle, *problem_flags, batches=3):
    r = run_cli("init_build", "-s", handle, "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--profile", str(batches), "--only", "all",
                *problem_flags)
    assert r.returncode == 0, r.stderr
    return r


def _ranking_cost(run_cli, handle, problem):
    r = run_cli("json_ranking_cost", "-s", handle, "--anchor", "none",
                "--problem", problem)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_ranking_cost_reflects_problem_input_size(run_cli, tmp_path):
    """The larger-input problem has a much higher real cost than the smaller one
    -- proving `build` runs each problem's own argv (a hard-wired `--estimate_all`
    would make both costs equal)."""
    cat_dir, handle = _bootstrap_two_problems(run_cli, tmp_path)
    _profile(run_cli, handle)                       # profiles small + large

    small = _ranking_cost(run_cli, handle, "problem.small")
    large = _ranking_cost(run_cli, handle, "problem.large")
    assert small["batch_count"] == 3 and large["batch_count"] == 3
    assert small["cost"] is not None and large["cost"] is not None
    # ~256x more work; a 3x margin is safe against noise but fails hard if both
    # problems ran at the same (estimate) size.
    assert large["cost"] > small["cost"] * 3, (small["cost"], large["cost"])


def test_build_problem_flag_selects_which_problem_runs(run_cli, tmp_path):
    """`build --problem problem.small` profiles ONLY that problem: the small set
    exists, the large one has no reachable batches."""
    cat_dir, handle = _bootstrap_two_problems(run_cli, tmp_path)
    _profile(run_cli, handle, "--problem", "problem.small")

    assert _ranking_cost(run_cli, handle, "problem.small")["batch_count"] == 3
    assert _ranking_cost(run_cli, handle, "problem.large")["batch_count"] == 0


def test_profiler_stats_reflects_problem_input_size(run_cli, tmp_path):
    """json_profiler_stats reports the selected problem's real numbers: the
    pipeline wall_time_mean is much larger for the large-input problem."""
    cat_dir, handle = _bootstrap_two_problems(run_cli, tmp_path)
    _profile(run_cli, handle, batches=2)

    def wall_time_mean(problem):
        r = run_cli("json_profiler_stats", "-s", handle, "--problem", problem,
                    "-p", "wall_time_mean")
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)["wall_time_mean"][1]   # median of [p25,med,p75]

    assert wall_time_mean("problem.large") > wall_time_mean("problem.small") * 3


def test_compare_cost_lists_every_enabled_problem(run_cli, tmp_path):
    """With two enabled problems and a 2-node build, json_compare_cost (no
    --problem) returns one comparison PER problem, each with real batches."""
    cat_dir, handle = _bootstrap_two_problems(run_cli, tmp_path)
    # Branch a fresh idea + perturb params so the target is a NEW node and `other`
    # is the seed canonical -> both profiled together, per problem.
    r = run_cli("new_idea", "-s", handle, "explore", "-", input="vary\n")
    assert r.returncode == 0, r.stderr
    idea = _line(r.stdout, "Created idea ")
    assert run_cli("set_idea", "-s", handle, idea).returncode == 0
    r = run_cli("workspace_parameters", "-s", handle)
    with open(r.stdout.strip(), "w") as f:
        json.dump([{"offset": 20}], f)
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "parent", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--profile", "2", "--only", "all")
    assert r.returncode == 0, r.stderr

    r = run_cli("json_compare_cost", "-s", handle)
    assert r.returncode == 0, r.stderr
    results = json.loads(r.stdout)
    by_problem = {x["problem_short_id"]: x for x in results}
    assert set(by_problem) == {"problem.small", "problem.large"}
    for x in results:
        assert x["batch_count"] == 2               # both problems really profiled
        assert x["result"] in ("improvement", "regression", "unknown")

    # Boolean form is the OR-fold of the per-problem verdicts.
    r = run_cli("json_compare_cost", "-s", handle, "--boolean")
    assert r.returncode == 0, r.stderr
    b = json.loads(r.stdout)
    assert set(b) == {"any_improvement", "any_regression", "any_unknown"}
    verdicts = {x["result"] for x in results}
    assert b["any_improvement"] == ("improvement" in verdicts)
    assert b["any_regression"] == ("regression" in verdicts)
    assert b["any_unknown"] == ("unknown" in verdicts)
