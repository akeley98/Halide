"""json_profiler_stats: the pure aggregation (`profiler_stats.aggregate`) and the
CLI tool wiring (parameters selection, hottest, reachability), driven from
synthetic benchmarks so it is deterministic and Halide-free (idea.md "JSON
Profiler Statistics Tool")."""

import json

import pytest

from dendritic_hl_lib import profiler_stats as ps, safety, tools
from dendritic_hl_lib.errors import DhHlError
from conftest import (add_synthetic_benchmark_set, make_profiler_obj,
                      open_catalog)


def _pipe(runs, time_ns, funcs, **extra):
    obj = {"profiler_version": 1, "name": "p", "runs": runs, "time_ns": time_ns,
           "num_allocs": 4, "active_threads_numerator": 10,
           "active_threads_denominator": 5, "memory_peak": 2048, "funcs": funcs}
    obj.update(extra)
    return obj


def _func(cid, name, parent, time_ns, **extra):
    d = {"canonical_id": cid, "name": name, "parent": parent, "time_ns": time_ns,
         "num_allocs": 2, "parallel_loops": 4, "parallel_tasks": 8,
         "active_threads_numerator": 6, "active_threads_denominator": 3,
         "memory_peak": 64}
    d.update(extra)
    return d


# ---- pure aggregation -----------------------------------------------------

def test_percentiles_and_special_values():
    p1 = _pipe(100, 1000, [_func(0, "a", -1, 600), _func(1, "b", 0, 400)])
    p2 = _pipe(100, 1000, [_func(0, "a", -1, 620), _func(1, "b", 0, 380)])
    out = ps.aggregate([p1, p2],
                       ["active_threads", "allocs_per_run", "memory_peak"],
                       ["allocs_per_run"])
    # Pipeline specials: 10/5, 4/100; a plain key passes through.
    assert out["active_threads"] == [2.0, 2.0, 2.0]
    assert out["allocs_per_run"] == [0.04, 0.04, 0.04]
    assert out["memory_peak"] == [2048, 2048, 2048]
    # time_ratio is implied and drives the sort (a hotter than b).
    names = [f["name"] for f in out["funcs"]]
    assert names == ["a", "b"]
    a = out["funcs"][0]
    assert a["name"] == "a" and a["parent"] == -1 and a["canonical_id"] == 0
    assert a["time_ratio"] == [0.605, 0.61, 0.615]     # 0.60 & 0.62
    assert a["allocs_per_run"] == [0.02, 0.02, 0.02]   # func 2/100


def test_func_key_order_is_name_parent_canonical_first():
    p = _pipe(10, 100, [_func(0, "a", -1, 60)])
    out = ps.aggregate([p], [], ["memory_peak"])
    assert list(out["funcs"][0])[:3] == ["name", "parent", "canonical_id"]


def test_time_ratio_always_included_even_if_not_requested():
    p = _pipe(10, 100, [_func(0, "a", -1, 60)])
    out = ps.aggregate([p], [], [])
    assert "time_ratio" in out["funcs"][0]


def test_hottest_truncates_after_sort():
    p = _pipe(10, 100, [_func(0, "cold", -1, 10), _func(1, "hot", -1, 90)])
    out = ps.aggregate([p], [], [], hottest=1)
    assert [f["name"] for f in out["funcs"]] == ["hot"]


def test_output_holds_exactly_the_requested_stats():
    """The output has EXACTLY the requested pipeline/func stats (plus the func
    identity keys and the always-on time_ratio) -- no extra keys leak in.  Guards
    against an aggregation accidentally computing/emitting an unrequested stat."""
    p = _pipe(10, 100, [_func(0, "a", -1, 60)])
    out = ps.aggregate([p], ["memory_peak"], ["num_allocs"])
    assert set(out) == {"memory_peak", "funcs"}
    assert set(out["funcs"][0]) == {"name", "parent", "canonical_id",
                                    "num_allocs", "time_ratio"}


def test_unknown_and_non_numeric_stats_error():
    p = _pipe(10, 100, [_func(0, "a", -1, 60)])
    with pytest.raises(DhHlError, match="no such pipeline statistic"):
        ps.aggregate([p], ["not_a_stat"], [])
    with pytest.raises(DhHlError, match="no such function statistic"):
        ps.aggregate([p], [], ["not_a_stat"])
    # "name" is a string value -> not a number.
    with pytest.raises(DhHlError, match="not a number"):
        ps.aggregate([p], [], ["name"])


def test_single_sample_degenerates_to_three_copies():
    p = _pipe(10, 100, [_func(0, "a", -1, 60)])
    out = ps.aggregate([p], ["memory_peak"], [])
    assert out["memory_peak"] == [2048, 2048, 2048]


def test_duplicate_requested_stat_deduped():
    p = _pipe(10, 100, [_func(0, "a", -1, 60)])
    out = ps.aggregate([p], ["memory_peak", "memory_peak"], [])
    # Only one pipeline key; still just the funcs list besides it.
    assert set(out) == {"memory_peak", "funcs"}


# ---- CLI tool (driven through the real `session` fixture) -----------------

def _bench(wtm, funcs, **extra):
    return make_profiler_obj(wtm, funcs=funcs, **extra)


def _child_schedule(session):
    """A fresh child schedule A under the session's seed idea."""
    from dendritic_hl_lib import locks
    cat = open_catalog(session.catalog_dir)
    try:
        idea = cat.get_idea(cat.get_session(session.session_id).seed_idea_id)
        A = cat.create_schedule("A source\n", parent_idea=idea)
        cat.flush(); safety.commit()
        return A.full_id
    finally:
        locks._reset_for_tests()


def _add_set(session, specs, **set_kw):
    from dendritic_hl_lib import locks
    from dendritic_hl_lib.context import SessionWorkspace
    cat = open_catalog(session.catalog_dir)
    try:
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        set_id = add_synthetic_benchmark_set(cat, specs, **set_kw)
        ws.add_private_benchmark_set(set_id, cat)
        cat.flush(); safety.commit()
        return set_id
    finally:
        locks._reset_for_tests()


def _stats_ns(session, A, **kw):
    kw.setdefault("f", None)
    kw.setdefault("p", None)
    kw.setdefault("parameters", None)
    kw.setdefault("hottest", None)
    return session.ns(schedule=A, **kw)


def test_tool_single_param_no_flag_needed(session, run_tool, capsys):
    A = _child_schedule(session)
    fs = [_func(0, "a", -1, 60), _func(1, "b", 0, 40)]
    _add_set(session, {A: [[
        _bench(100, fs, runs=10, time_ns=100, num_allocs=4,
               active_threads_numerator=10, active_threads_denominator=5),
        _bench(102, fs, runs=10, time_ns=100, num_allocs=4,
               active_threads_numerator=10, active_threads_denominator=5)]]})
    out = _out(run_tool, capsys, tools.cmd_json_profiler_stats,
               _stats_ns(session, A, f=["allocs_per_run"], p=["active_threads"]))
    obj = json.loads(out)
    assert obj["active_threads"] == [2.0, 2.0, 2.0]
    assert [f["name"] for f in obj["funcs"]] == ["a", "b"]  # sorted by time_ratio
    assert obj["funcs"][0]["allocs_per_run"] == [0.2, 0.2, 0.2]  # func 2/10


def test_tool_requires_parameters_when_multiple(session, run_tool, capsys):
    A = _child_schedule(session)
    fs = [_func(0, "a", -1, 60)]
    _add_set(session, {A: [[_bench(100, fs, runs=10, time_ns=100)],
                           [_bench(70, fs, runs=10, time_ns=100)]]})
    with pytest.raises(DhHlError, match="--parameters is required"):
        run_tool(tools.cmd_json_profiler_stats, _stats_ns(session, A))
    out = _out(run_tool, capsys, tools.cmd_json_profiler_stats,
               _stats_ns(session, A, parameters=1))
    assert json.loads(out)["funcs"][0]["name"] == "a"


def test_tool_parameters_selects_the_named_object(session, run_tool, capsys):
    """--parameters N must select the N-th params object's benchmarks, not just
    be accepted.  The two objects carry DISTINCT per-func values so a mis-select
    (e.g. always reading index 0) is caught."""
    A = _child_schedule(session)
    par = [_func(0, "loop", -1, 60, parallel_loops=100)]   # params index 0
    ser = [_func(0, "loop", -1, 60, parallel_loops=0)]     # params index 1
    _add_set(session, {A: [[_bench(100, par, runs=10, time_ns=100)],
                           [_bench(140, ser, runs=10, time_ns=100)]]})

    def loops(pidx):
        out = _out(run_tool, capsys, tools.cmd_json_profiler_stats,
                   _stats_ns(session, A, parameters=pidx, f=["parallel_loops"]))
        return json.loads(out)["funcs"][0]["parallel_loops"][1]  # median

    assert loops(0) == 100     # params index 0
    assert loops(1) == 0       # params index 1 -> different data, really selected


def test_tool_no_reachable_benchmarks(session, run_tool):
    A = _child_schedule(session)  # no benchmark set added
    with pytest.raises(DhHlError, match="no benchmarks reachable"):
        run_tool(tools.cmd_json_profiler_stats, _stats_ns(session, A))


def test_tool_hottest_and_session_lock(session, run_tool, capsys):
    from dendritic_hl_lib import locks
    A = _child_schedule(session)
    fs = [_func(0, "cold", -1, 10), _func(1, "hot", -1, 90)]
    _add_set(session, {A: [[_bench(100, fs, runs=10, time_ns=100)]]})
    out = _out(run_tool, capsys, tools.cmd_json_profiler_stats,
               _stats_ns(session, A, hottest=1))
    assert [f["name"] for f in json.loads(out)["funcs"]] == ["hot"]
    assert ("session", "exclusive") in locks._trace_sink


def _out(run_tool, capsys, fn, args):
    capsys.readouterr()
    run_tool(fn, args)
    return capsys.readouterr().out


def test_tool_problem_filter(session, run_tool, capsys):
    """--problem selects which problem's benchmarks feed the stats; default is
    the main problem (idea.md json_profiler_stats)."""
    A = _child_schedule(session)
    main_id = _out(run_tool, capsys, tools.cmd_problem_full_id,
                   session.ns(problem="main")).strip()
    run_tool(tools.cmd_new_problem,
             session.ns(short_name="big", argv=["<RunGenMain>", "--big"]))
    big_id = _out(run_tool, capsys, tools.cmd_problem_full_id,
                  session.ns(problem="problem.big")).strip()
    # Different funcs per problem so the selection is observable.
    _add_set(session, {A: [[_bench(100, [_func(0, "a", -1, 90)],
                                   runs=1, time_ns=100)]]}, problem=main_id)
    _add_set(session, {A: [[_bench(100, [_func(0, "z", -1, 90)],
                                   runs=1, time_ns=100)]]}, problem=big_id)

    out = _out(run_tool, capsys, tools.cmd_json_profiler_stats,
               _stats_ns(session, A))                      # default -> main
    assert [f["name"] for f in json.loads(out)["funcs"]] == ["a"]
    out = _out(run_tool, capsys, tools.cmd_json_profiler_stats,
               _stats_ns(session, A, problem="problem.big"))
    assert [f["name"] for f in json.loads(out)["funcs"]] == ["z"]
