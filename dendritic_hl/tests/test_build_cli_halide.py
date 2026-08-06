"""Real-Halide, real-CLI (`run_cli`) coverage for the `build` tool, using the
tests/hist_params.cpp generator whose params (enable_parallel / print_me /
should_fail) drive three checks:

* profiler stats are attributed to the right parameters object (the parallel
  variant is clearly faster than the serial one);
* the generator's stdout is ordered correctly against the harness `dh_hl:`
  generator banners;
* a failing generator yields the right success/fail banners, a `halide error`
  result, a nonzero exit, and NO benchmark set.

Opt-in (marked `halide`): needs the local ~/Halide build + ninja.  Everything
goes through the real `./dh_hl` subprocess -- no in-process helpers, no
monkeypatching.
"""

import json
import os
import shutil

import pytest

from dendritic_hl_lib import build
from conftest import _PKG_ROOT

pytestmark = [
    pytest.mark.halide,
    pytest.mark.skipif(not os.path.isdir(build.HALIDE_BUILD),
                       reason="no local Halide build at " + build.HALIDE_BUILD),
    pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not found"),
]

_HISTP = os.path.join(_PKG_ROOT, "tests", "hist_params.cpp")


def _line(out, prefix):
    for ln in out.splitlines():
        if ln.startswith(prefix):
            return ln[len(prefix):].strip()
    raise AssertionError("no line starting {!r} in:\n{}".format(prefix, out))


def _bootstrap(run_cli, tmp_path):
    """new_catalog + init_workspace through the CLI; return (cat_dir, handle)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "p.txt").write_text("explore histp\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed",
                str(tmp_path / "p.txt"), _HISTP)
    assert r.returncode == 0, r.stderr
    handle = _line(r.stdout, "Session handle: ")
    r = run_cli("init_workspace", "-s", handle)
    assert r.returncode == 0, r.stderr
    return cat_dir, handle


def _branch_fresh_idea(run_cli, handle):
    """Real explore-a-change workflow: the seed idea already has a canonical, so
    branch a fresh idea off it (the workspace is still consistent right after
    bootstrap, so new_idea's default schedule is the seed canonical) and make it
    current.  A later perturbed `init_build --target workspace` then creates a
    child under the new, canonical-less idea -- init_build refuses to add children
    to an idea that already has a canonical (idea.md "Init-Build Tool").  Call
    this BEFORE perturbing the workspace params."""
    r = run_cli("new_idea", "-s", handle, "explore", "-",
                input="explore variation\n")
    assert r.returncode == 0, r.stderr
    idea = _line(r.stdout, "Created idea ")
    r = run_cli("set_idea", "-s", handle, idea)
    assert r.returncode == 0, r.stderr


def _set_params(run_cli, handle, params):
    """Overwrite the workspace generator_parameters.json (list of objects)."""
    r = run_cli("workspace_parameters", "-s", handle)
    assert r.returncode == 0, r.stderr
    with open(r.stdout.strip(), "w") as f:
        json.dump(params, f)


def _schedule_json(run_cli, handle):
    r = run_cli("json_schedule_info", "-s", handle)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _per_run_ns(bench):
    p = bench["profiler"]
    return p["time_ns"] / max(p["billed_runs"], 1)


def _workspace_short_id(run_cli, handle):
    """The workspace schedule node's short ID, as `status` reports it (a
    deterministic value -- hash prefix + idea path -- with no timestamp)."""
    r = run_cli("status", "-s", handle)
    assert r.returncode == 0, r.stderr
    return _line(r.stdout, "Schedule node:")


# ---------------------------------------------------------------------------

def test_parallel_param_is_faster_than_serial(run_cli, tmp_path):
    """The profiler stats must track the parameters object: the enable_parallel
    variant is meaningfully faster than the serial one."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _branch_fresh_idea(run_cli, handle)
    _set_params(run_cli, handle,
                [{"enable_parallel": True}, {"enable_parallel": False}])
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--profile", "3", "--only", "all")
    assert r.returncode == 0, r.stderr

    benches = _schedule_json(run_cli, handle)["benchmark"]
    assert len(benches) == 6  # 2 parameters objects x 3 profiler batches
    per_param = {True: [], False: []}
    for b in benches:
        per_param[b["parameters"]["enable_parallel"]].append(_per_run_ns(b))
    parallel = min(per_param[True])   # best case per variant, to fight noise
    serial = min(per_param[False])
    # Multi-core: parallel is much faster.  A margin guards against noise while
    # still failing loudly if the stats were attributed to the wrong param.
    assert parallel < serial, (parallel, serial)
    assert serial > parallel * 1.5, (parallel, serial)


def test_generator_print_ordered_within_banners(run_cli, tmp_path):
    """The generator's stdout appears between the harness's begin/end generator
    banners (the flush ordering in build.py must hold under a piped CLI)."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _branch_fresh_idea(run_cli, handle)
    _set_params(run_cli, handle, [{"print_me": "HELLOWORLD"}])
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--only", "all")   # no profiling needed
    assert r.returncode == 0, r.stderr

    lines = r.stdout.splitlines()
    begin = next(i for i, l in enumerate(lines)
                 if l.startswith("dh_hl: begin Halide generator 0"))
    printed = next(i for i, l in enumerate(lines) if l == "GEN_PRINT: HELLOWORLD")
    end = next(i for i, l in enumerate(lines)
               if l.startswith("dh_hl: end Halide generator 0"))
    assert begin < printed < end, lines


def test_profiling_stdout_redirected_then_viewable(run_cli, tmp_path):
    """The profiling run's stdout is redirected into the benchmark sub-object,
    NOT echoed to the harness stdout, and comes back out via
    `view_benchmark_stdout` (idea.md Build Tool + View Benchmark Stdout Tool).

    RunGenMain prints its benchmark result to stdout ("... produces best case of
    ... sec/iter ...", "Best output throughput is ..."); we assert that text is
    absent from `build`'s stdout but present in the viewed benchmark stdout."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _branch_fresh_idea(run_cli, handle)
    _set_params(run_cli, handle, [{"enable_parallel": True}])
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--profile", "1", "--only", "all")
    assert r.returncode == 0, r.stderr
    # The run's stdout is redirected, so the harness output must NOT carry it.
    assert "produces best case of" not in r.stdout
    assert "Best output throughput is" not in r.stdout

    # The `dh_hl: ... with Benchmark ID:` line prints a resolvable (short) ID.
    bench_id = _line(r.stdout, "dh_hl: ... with Benchmark ID: ")
    r = run_cli("view_benchmark_stdout", "-C", cat_dir, bench_id)
    assert r.returncode == 0, r.stderr
    assert "produces best case of" in r.stdout
    assert "Best output throughput is" in r.stdout
    # The `halide_print:` profiler-stats table is intentionally kept in the
    # captured stdout (an easy read next to the JSON tools).
    assert "halide_print:" in r.stdout
    assert "total time:" in r.stdout


def test_build_banners_use_short_schedule_ids(run_cli, tmp_path):
    """`build`'s per-node banners print the schedule's SHORT ID, not the full ID
    (idea.md Build Tool pseudocode uses `{node.short_id}`).  The short ID is
    deterministic (no timestamp), so we can assert exact banner lines: a
    regression to the full ID (which starts with a creation timestamp) would
    fail these.  Covers the compile phase (lock-free, uses the precomputed short
    ID) and the profile phase (formats live under the catalog lock)."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    short = _workspace_short_id(run_cli, handle)
    # A full ID is "{timestamp}_{hash}[...]"; the short form's ".seed.canon"
    # path can't appear there, so this confirms we're asserting a short ID.
    assert ".seed.canon" in short

    # Leave the workspace params at their default so --target workspace resolves
    # to the existing seed canonical (a stable short ID); changing params would
    # mint a fresh node with a different short ID.
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--profile", "1", "--only", "all")
    assert r.returncode == 0, r.stderr

    assert "dh_hl: begin C++ compile: {}".format(short) in r.stdout
    assert "dh_hl: begin Halide generator 0: {}".format(short) in r.stdout
    assert "dh_hl: Profiled {}, binary 0 (success)".format(short) in r.stdout
    # The per-benchmark ID is the short schedule ID plus a {hostname}_{ts} tail.
    assert "dh_hl: ... with Benchmark ID: {}.".format(short) in r.stdout


def test_benchmark_set_cells_attributed(run_cli, tmp_path):
    """Each benchmark-set cell references a benchmark that actually belongs to
    that (schedule, parameters index): the benchmark full ID is prefixed by the
    schedule full ID, and its recorded parameters match the node's params[i]."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _branch_fresh_idea(run_cli, handle)
    _set_params(run_cli, handle,
                [{"enable_parallel": True}, {"enable_parallel": False}])
    # target (2 params) + other = the seed canonical (1 param) -> a 2-node set.
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "parent", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--profile", "2", "--only", "all")
    assert r.returncode == 0, r.stderr
    set_id = _line(r.stdout, "dh_hl: Benchmark set ID: ")

    r = run_cli("json_benchmark_set_info", "-C", cat_dir, set_id)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert len(data) == 2  # target + other schedule nodes

    for sched_id, per_param in data.items():
        r = run_cli("json_schedule_info", "-C", cat_dir, sched_id)
        assert r.returncode == 0, r.stderr
        node_params = json.loads(r.stdout)["parameters"]
        assert len(per_param) == len(node_params)
        for pidx, batch_ids in enumerate(per_param):
            assert len(batch_ids) == 2  # 2 batches
            for bid in batch_ids:
                # The benchmark is filed under the right schedule...
                assert bid.startswith(sched_id), (bid, sched_id)
                # ...and carries the params for THIS index.
                r = run_cli("json_benchmark_info", "-C", cat_dir, bid)
                assert r.returncode == 0, r.stderr
                assert json.loads(r.stdout)["parameters"] == node_params[pidx]


def test_failed_generator_no_benchmark_set(run_cli, tmp_path):
    """A should_fail param: its generator fails (success banner for the other,
    fail banner for it), the node result caps at `halide error`, the run exits
    nonzero, and NO benchmark set is produced."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _branch_fresh_idea(run_cli, handle)
    _set_params(run_cli, handle,
                [{"enable_parallel": True}, {"should_fail": True}])
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--profile", "1", "--only", "all")
    assert r.returncode == 1, "a failed generator must exit nonzero"
    out = r.stdout
    assert "dh_hl: end Halide generator 0 success" in out
    assert "dh_hl: end Halide generator 1 fail" in out
    # A failed subprocess means no benchmark set is created (idea.md Build Tool).
    assert "Benchmark set ID:" not in out
    # The node result is capped at halide error (a generator failed).
    assert _schedule_json(run_cli, handle)["result"] == "halide error"


# ---------------------------------------------------------------------------
# Cost tools over real profiler numbers (same real-CLI, real-Halide style).
# The parallel variant is much faster than the serial one; the tools must see
# that through the batched profiling.
# ---------------------------------------------------------------------------

def _profile_target(run_cli, handle, params, *, other="none", batches=3):
    """Branch a fresh idea (so init_build may create a child), set the workspace
    params, init_build, and `build --profile` the target (a fresh node, since
    non-default params change its hash).  Returns nothing; the produced benchmark
    set lands in the session's private list.  With other='parent' the target's
    'other' node is the seed canonical (the new idea's parent schedule)."""
    _branch_fresh_idea(run_cli, handle)
    _set_params(run_cli, handle, params)
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", other, "--anchor", "none")
    assert r.returncode == 0, r.stderr
    r = run_cli("build", "-s", handle, "--profile", str(batches), "--only", "all")
    assert r.returncode == 0, r.stderr


def test_json_ranking_cost_representative_prefers_parallel(run_cli, tmp_path):
    """json_ranking_cost picks the faster parameters object as the representative
    and reports its raw cost (no anchor -> raw wall_time_min)."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _profile_target(run_cli, handle,
                    [{"enable_parallel": True}, {"enable_parallel": False}])

    r = run_cli("json_ranking_cost", "-s", handle, "--anchor", "none")
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["anchor"] is None and obj["batch_count"] == 3
    assert obj["representative"] == 0                 # parallel (index 0) is faster
    raw = obj["parameters_raw_cost"]
    assert len(raw) == 2 and raw[0] < raw[1]         # parallel < serial
    assert obj["cost"] == raw[0]                      # rep's raw cost


def test_json_profiler_stats_aggregates_real_funcs(run_cli, tmp_path):
    """json_profiler_stats aggregates real per-func profiler samples into
    [p25, median, p75], sorted hottest-first, with --parameters mandatory when
    the node has >1 params object."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _profile_target(run_cli, handle,
                    [{"enable_parallel": True}, {"enable_parallel": False}],
                    batches=2)

    # Two params objects -> --parameters is required.
    r = run_cli("json_profiler_stats", "-s", handle)
    assert r.returncode != 0 and "--parameters" in r.stderr

    r = run_cli("json_profiler_stats", "-s", handle, "--parameters", "0",
                "-p", "wall_time_mean", "-f", "recompute_ratio", "--hottest", "4")
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    # Pipeline-global stat -> a 3-number percentile list.
    assert isinstance(obj["wall_time_mean"], list) and len(obj["wall_time_mean"]) == 3
    # Exactly the requested pipeline stat + funcs -- no unrequested stat leaks in.
    assert set(obj) == {"wall_time_mean", "funcs"}
    funcs = obj["funcs"]
    assert 1 <= len(funcs) <= 4                       # truncated to the 4 hottest
    medians = [f["time_ratio"][1] for f in funcs]
    assert medians == sorted(medians, reverse=True)   # sorted by median time_ratio
    for f in funcs:
        # EXACTLY the func identity keys + the two requested per-func stats.
        assert set(f) == {"name", "parent", "canonical_id", "time_ratio",
                          "recompute_ratio"}
        assert len(f["time_ratio"]) == 3 and len(f["recompute_ratio"]) == 3


def test_json_profiler_stats_parameters_selects_parallelism(run_cli, tmp_path):
    """--parameters actually selects the named params object's benchmarks: the
    parallel variant reports parallel loops, the serial one reports none.  A
    mis-select (e.g. always reading index 0) flips one of these assertions."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    # index 0 = parallel, index 1 = serial.
    _profile_target(run_cli, handle,
                    [{"enable_parallel": True}, {"enable_parallel": False}],
                    batches=2)

    def total_parallel_loops(pidx):
        r = run_cli("json_profiler_stats", "-s", handle, "--parameters", str(pidx),
                    "-f", "parallel_loops")
        assert r.returncode == 0, r.stderr
        # Sum the median parallel_loops over funcs; parallelized funcs are > 0.
        return sum(f["parallel_loops"][1] for f in json.loads(r.stdout)["funcs"])

    assert total_parallel_loops(0) > 0     # parallel variant runs parallel loops
    assert total_parallel_loops(1) == 0    # serial variant runs none


def test_json_compare_cost_detects_regression(run_cli, tmp_path):
    """A serial target vs the parallel (default) seed canonical: the 2-way
    comparison confidently reports a regression (LHS is the dearer serial
    schedule)."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    # Serial target; other=parent pulls in the seed canonical (default params ->
    # parallel), so both are profiled in the same batches (the paired comparison
    # needs that).  The seed canonical is also json_compare_cost's default RHS
    # (the parent schedule of the LHS's parent idea).
    _profile_target(run_cli, handle, [{"enable_parallel": False}], other="parent")

    # LHS defaults to the workspace (serial target); RHS defaults to the seed
    # canonical (parent of the LHS's parent idea).
    r = run_cli("json_compare_cost", "-s", handle)
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["batch_count"] == 3
    assert obj["result"] == "regression"             # serial LHS slower than root
    assert obj["lhs_raw_cost"] > obj["rhs_raw_cost"]
