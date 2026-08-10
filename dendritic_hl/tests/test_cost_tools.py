"""CLI-level coverage of json_ranking_cost / json_compare_cost (idea.md), driven
in-process through run_tool so the real session/catalog locks + arg defaulting
run.  The cost math itself is covered by test_cost.py; here we pin the tool
wiring: anchor resolution, LHS/RHS defaulting, the JSON output shape, and the
private-benchmark-set-list plumbing.

The `session` fixture's seed idea has a canonical schedule (`dup`) whose parent
is the root; we hang extra child schedules off the seed idea and benchmark them
so the tools have real data."""

import json

import pytest

from dendritic_hl_lib import cost, safety, tools
from dendritic_hl_lib.errors import DhHlError
from conftest import add_synthetic_benchmark_set, open_catalog, ns


def _out(run_tool, capsys, fn, args):
    capsys.readouterr()
    run_tool(fn, args)
    return capsys.readouterr().out


def _setup(session, specs, *, anchor=None, **set_kw):
    """Add a benchmark set over *specs* to the session's private list (as build
    would), optionally set the current anchor, and return the id map.  *specs*
    maps a symbolic name -> schedule full ID resolver output."""
    from dendritic_hl_lib import locks
    cat = open_catalog(session.catalog_dir)
    try:
        from dendritic_hl_lib.context import SessionWorkspace
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        set_id = add_synthetic_benchmark_set(cat, specs, **set_kw)
        ws.add_private_benchmark_set(set_id, cat)
        if anchor is not None:
            ws.set_current_anchor(anchor)
        cat.flush()
        safety.commit()
        return set_id
    finally:
        locks._reset_for_tests()


def _seed_children(session):
    """Create two extra child schedules A and B under the seed idea (so they are
    siblings of the seed canonical `dup`), returning their full IDs + the
    canonical (as a convenient anchor)."""
    from dendritic_hl_lib import locks
    cat = open_catalog(session.catalog_dir)
    try:
        seed = cat.get_session(session.session_id).seed_idea_id
        idea = cat.get_idea(seed)
        canon = idea.canonical
        A = cat.create_schedule("A source\n", parent_idea=idea)
        B = cat.create_schedule("B source\n", parent_idea=idea)
        cat.flush()
        safety.commit()
        return {"A": A.full_id, "B": B.full_id, "canon": canon,
                "idea": idea.full_id}
    finally:
        locks._reset_for_tests()


# ---- json_ranking_cost ----------------------------------------------------

def test_ranking_cost_no_anchor(session, run_tool, capsys):
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99]]})
    out = _out(run_tool, capsys, tools.cmd_json_ranking_cost,
               session.ns(schedule=t["A"], anchor="none"))
    obj = json.loads(out)
    assert obj == {"batch_count": 3, "cost": 100, "anchor": None,
                   "representative": 0, "parameters_raw_cost": [100]}


def test_ranking_cost_auto_uses_current_anchor(session, run_tool, capsys):
    t = _seed_children(session)
    # A and the anchor (canon) share a set; A is half the anchor's cost.
    _setup(session, {t["A"]: [[100, 100, 100]], t["canon"]: [[200, 200, 200]]},
           anchor=t["canon"])
    # --anchor auto (default) picks up the current anchor -> ratio cost.
    out = _out(run_tool, capsys, tools.cmd_json_ranking_cost,
               session.ns(schedule=t["A"], anchor="auto"))
    obj = json.loads(out)
    assert obj["anchor"] == t["canon"]
    assert obj["cost"] == 0.5
    assert obj["parameters_raw_cost"] == [100]  # raw, not ratio


def test_ranking_cost_always_requires_anchor(session, run_tool):
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99]]})  # no anchor set
    with pytest.raises(DhHlError, match="no current anchor"):
        run_tool(tools.cmd_json_ranking_cost,
                 session.ns(schedule=t["A"], anchor="always"))


def test_ranking_cost_null_when_no_batches(session, run_tool, capsys):
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99]]})
    # B is never benchmarked -> null cost, null representative, [null] per param.
    out = _out(run_tool, capsys, tools.cmd_json_ranking_cost,
               session.ns(schedule=t["B"], anchor="none"))
    obj = json.loads(out)
    assert obj == {"batch_count": 0, "cost": None, "anchor": None,
                   "representative": None, "parameters_raw_cost": [None]}


def _short_id(session, full_id):
    from dendritic_hl_lib import locks
    cat = open_catalog(session.catalog_dir)
    try:
        return cat.format_schedule_id(cat.get_schedule(full_id))
    finally:
        locks._reset_for_tests()


def test_ranking_cost_zero_batches_warns_to_profile(session, run_tool, capsys):
    """idea.md: 0 batches -> a stderr warning suggesting a correct init_build +
    build --profile sequence targeting the schedule.  With --anchor auto (the
    default) the suggestion omits --anchor."""
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99]]})  # B is never benchmarked
    capsys.readouterr()
    run_tool(tools.cmd_json_ranking_cost, session.ns(schedule=t["B"]))  # default anchor
    err = capsys.readouterr().err
    short_b = _short_id(session, t["B"])
    assert "no benchmark batches" in err
    assert "dh_hl init_build --target {}".format(short_b) in err
    assert "dh_hl build --profile" in err
    assert "--anchor" not in err  # auto in effect -> omitted


def test_ranking_cost_zero_batches_warning_echoes_explicit_anchor(
        session, run_tool, capsys):
    """An explicit (non-auto) --anchor is echoed in the suggestion."""
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99]]})
    capsys.readouterr()
    run_tool(tools.cmd_json_ranking_cost,
             session.ns(schedule=t["B"], anchor="none"))
    err = capsys.readouterr().err
    assert "--anchor none" in err


def test_ranking_cost_with_batches_gives_no_warning(session, run_tool, capsys):
    """The warning is only for the 0-batch case."""
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99]]})
    capsys.readouterr()
    run_tool(tools.cmd_json_ranking_cost,
             session.ns(schedule=t["A"], anchor="none"))
    assert capsys.readouterr().err == ""


# ---- json_compare_cost ----------------------------------------------------

def test_compare_explicit_lhs_rhs(session, run_tool, capsys):
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99, 100, 102]],
                     t["B"]: [[130, 131, 129, 130, 128]]})
    out = _out(run_tool, capsys, tools.cmd_json_compare_cost,
               session.ns(lhs=t["A"], rhs=t["B"]))
    results = json.loads(out)
    # One per-problem comparison (the session's default main problem).
    assert len(results) == 1
    obj = results[0]
    assert obj["problem_short_id"] == "problem.default"
    assert obj["problem"] and obj["problem_short_id"]
    assert obj["result"] == "improvement" and obj["batch_count"] == 5
    assert obj["lhs_raw_cost"] == 100 and obj["rhs_raw_cost"] == 130
    assert obj["lhs_representative"] == 0 and obj["rhs_representative"] == 0


def test_compare_default_rhs_is_parent_of_parent_idea(session, run_tool, capsys):
    """With no RHS, the baseline is the parent schedule of LHS's parent idea.
    A's parent idea is the seed idea; its parent schedule is the root, so we
    benchmark A against the root."""
    from dendritic_hl_lib import locks
    t = _seed_children(session)
    cat = open_catalog(session.catalog_dir)
    try:
        root = cat.get_idea(t["idea"]).parent_schedule().full_id
    finally:
        locks._reset_for_tests()
    _setup(session, {t["A"]: [[90, 91, 89, 90, 92]],
                     root: [[130, 129, 131, 130, 128]]})
    out = _out(run_tool, capsys, tools.cmd_json_compare_cost,
               session.ns(lhs=t["A"]))  # rhs defaulted
    obj = json.loads(out)[0]
    assert obj["result"] == "improvement"
    assert obj["rhs_raw_cost"] == 130  # the root baseline


def test_compare_root_lhs_without_rhs_errors(session, run_tool):
    from dendritic_hl_lib import locks
    t = _seed_children(session)
    cat = open_catalog(session.catalog_dir)
    try:
        root = cat.get_idea(t["idea"]).parent_schedule().full_id
    finally:
        locks._reset_for_tests()
    _setup(session, {root: [[100, 101, 99]]})
    with pytest.raises(DhHlError, match="root schedule"):
        run_tool(tools.cmd_json_compare_cost, session.ns(lhs=root))


def test_compare_confidence_validation(session, run_tool):
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99]], t["B"]: [[130, 131, 129]]})
    for bad in (0.0, 1.0, 1.5, -0.1):
        with pytest.raises(DhHlError, match="0 < ci < 1"):
            run_tool(tools.cmd_json_compare_cost,
                     session.ns(lhs=t["A"], rhs=t["B"], confidence=bad))


def test_compare_bootstrap_switch(session, run_tool, capsys):
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99, 100, 102]],
                     t["B"]: [[130, 131, 129, 130, 128]]})
    # A low --bootstrap still works and yields the same verdict here.
    out = _out(run_tool, capsys, tools.cmd_json_compare_cost,
               session.ns(lhs=t["A"], rhs=t["B"], bootstrap=200))
    assert json.loads(out)[0]["result"] == "improvement"
    with pytest.raises(DhHlError, match="at least 2"):
        run_tool(tools.cmd_json_compare_cost,
                 session.ns(lhs=t["A"], rhs=t["B"], bootstrap=1))


def test_ranking_cost_takes_session_lock(session, run_tool):
    """Reads private-workspace state, so it must acquire the session lock
    (impl.md Lock Hierarchy) -- verified via the lock trace."""
    from dendritic_hl_lib import locks
    t = _seed_children(session)
    _setup(session, {t["A"]: [[100, 101, 99]]})
    run_tool(tools.cmd_json_ranking_cost,
             session.ns(schedule=t["A"], anchor="none"))
    assert ("session", "exclusive") in locks._trace_sink


# ---- per-problem cost (2f) ------------------------------------------------

def _pid(run_tool, capsys, session, spec):
    return _out(run_tool, capsys, tools.cmd_problem_full_id,
                session.ns(problem=spec)).strip()


def _add_second_problem(session, run_tool, capsys):
    run_tool(tools.cmd_new_problem,
             session.ns(short_name="big", argv=["<RunGenMain>", "--big"]))
    return (_pid(run_tool, capsys, session, "main"),
            _pid(run_tool, capsys, session, "problem.big"))


def test_ranking_cost_problem_filter(session, run_tool, capsys):
    """--problem selects which problem's benchmark sets feed the cost; the
    default is the main problem."""
    t = _seed_children(session)
    main_id, big_id = _add_second_problem(session, run_tool, capsys)
    _setup(session, {t["A"]: [[100, 100, 100]]}, problem=main_id)
    _setup(session, {t["A"]: [[300, 300, 300]]}, problem=big_id)

    out = _out(run_tool, capsys, tools.cmd_json_ranking_cost,
               session.ns(schedule=t["A"], anchor="none"))
    assert json.loads(out)["cost"] == 100          # default -> main problem
    out = _out(run_tool, capsys, tools.cmd_json_ranking_cost,
               session.ns(schedule=t["A"], anchor="none", problem="problem.big"))
    assert json.loads(out)["cost"] == 300          # --problem big


def test_compare_cost_per_problem_list_and_boolean(session, run_tool, capsys):
    """With no --problem, json_compare_cost runs once per enabled problem and
    returns a list; --boolean collapses to the any_* summary."""
    t = _seed_children(session)
    main_id, big_id = _add_second_problem(session, run_tool, capsys)
    # main: A cheaper than B (improvement); big: A dearer than B (regression).
    _setup(session, {t["A"]: [[100] * 5], t["B"]: [[130] * 5]}, problem=main_id)
    _setup(session, {t["A"]: [[300] * 5], t["B"]: [[130] * 5]}, problem=big_id)

    out = _out(run_tool, capsys, tools.cmd_json_compare_cost,
               session.ns(lhs=t["A"], rhs=t["B"]))
    results = {r["problem_short_id"]: r["result"] for r in json.loads(out)}
    assert results == {"problem.default": "improvement",
                       "problem.big": "regression"}

    out = _out(run_tool, capsys, tools.cmd_json_compare_cost,
               session.ns(lhs=t["A"], rhs=t["B"], boolean=True))
    assert json.loads(out) == {"any_improvement": True, "any_regression": True,
                               "any_unknown": False}


def test_compare_cost_single_problem_via_flag(session, run_tool, capsys):
    """--problem restricts json_compare_cost to the named problem only."""
    t = _seed_children(session)
    main_id, big_id = _add_second_problem(session, run_tool, capsys)
    _setup(session, {t["A"]: [[100] * 5], t["B"]: [[130] * 5]}, problem=main_id)
    _setup(session, {t["A"]: [[300] * 5], t["B"]: [[130] * 5]}, problem=big_id)
    out = _out(run_tool, capsys, tools.cmd_json_compare_cost,
               session.ns(lhs=t["A"], rhs=t["B"], problem=["problem.big"]))
    results = json.loads(out)
    assert len(results) == 1 and results[0]["problem_short_id"] == "problem.big"
    assert results[0]["result"] == "regression"


def test_ranking_cost_zero_batches_verbose_breakdown(session, run_tool, capsys):
    """A 0-batch cost query prints a stderr breakdown showing where the batches
    were lost -- here A has batches for main but none for the queried problem."""
    t = _seed_children(session)
    _add_second_problem(session, run_tool, capsys)
    _setup(session, {t["A"]: [[100, 100, 100]]})       # tagged main (default)
    capsys.readouterr()
    run_tool(tools.cmd_json_ranking_cost,
             session.ns(schedule=t["A"], anchor="none", problem="problem.big"))
    cap = capsys.readouterr()
    assert json.loads(cap.out)["batch_count"] == 0
    assert "Reachable batch breakdown" in cap.err
    assert "also requiring problem problem.big: 0" in cap.err
    assert "dh_hl build --profile ... --problem problem.big" in cap.err
