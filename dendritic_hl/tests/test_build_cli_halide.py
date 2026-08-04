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


# ---------------------------------------------------------------------------

def test_parallel_param_is_faster_than_serial(run_cli, tmp_path):
    """The profiler stats must track the parameters object: the enable_parallel
    variant is meaningfully faster than the serial one."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
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


def test_benchmark_set_cells_attributed(run_cli, tmp_path):
    """Each benchmark-set cell references a benchmark that actually belongs to
    that (schedule, parameters index): the benchmark full ID is prefixed by the
    schedule full ID, and its recorded parameters match the node's params[i]."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _set_params(run_cli, handle,
                [{"enable_parallel": True}, {"enable_parallel": False}])
    # target (2 params) + other = the root (1 param) -> a 2-node set.
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
