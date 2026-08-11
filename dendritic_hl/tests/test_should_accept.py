"""should_accept + close_session override coverage (idea.md "Should-accept
Schedule Tool" / "Close Session Tool").  Verifies the failed-problem check runs
for every session, the golden/deleted-problem checks run for TOP-LEVEL sessions
only, and the close_session --allow-* overrides.  Halide-free: benchmarks and
algorithm-hlpipe artifacts are fabricated directly."""

import os

import pytest

from dendritic_hl_lib import build, locks, safety, tools
from dendritic_hl_lib.context import SessionWorkspace
from dendritic_hl_lib.enums import ProblemState
from dendritic_hl_lib.errors import DhHlError
from conftest import (DUMMY_SOURCE, add_synthetic_benchmark_set, ns,
                      open_catalog)


def _reset():
    locks._reset_for_tests()


# ---- fabrication helpers --------------------------------------------------

def _init_ws(cat, sess_node, source=DUMMY_SOURCE):
    ws = SessionWorkspace(cat.catalog_dir, sess_node.full_id, catalog=cat)
    ws.initialize(source, ("idea", sess_node.seed_idea_id))
    ws.set_pool_tag(sess_node.seed_idea_id, "default")
    return ws


def _cover(cat, ws, sched_id, problem_id, nparams=1):
    """Give (sched, each of nparams, problem) a benchmark in ws's private list."""
    set_id = add_synthetic_benchmark_set(
        cat, {sched_id: [[1.0] for _ in range(nparams)]}, problem=problem_id)
    ws.add_private_benchmark_set(set_id, catalog=cat)
    return set_id


def _fake_hlpipe(cat_dir, sess_id, sched_id, content=b"algo\n"):
    rel = build._build_output_rel(sched_id, "algorithm_hlpipe", 0)
    p = os.path.join(cat_dir, "private", sess_id, "bin", rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(content)


def _flags(out):
    """The set of override flags should_accept says are needed (empty if all
    checks passed)."""
    for line in out.splitlines():
        if line.startswith("Overrides needed for close_session:"):
            return set(line.split(":", 1)[1].split())
    assert "All checks passed" in out, out
    return set()


def _accept(run_tool, capsys, cat_dir, sess_id, schedule):
    run_tool(tools.cmd_should_accept,
             ns(session=sess_id, catalog=cat_dir, schedule=schedule))
    return _flags(capsys.readouterr().out)


# ---- failed problem check (all sessions) ----------------------------------

def test_failed_problem_check(session, run_tool, capsys):
    cat = open_catalog(session.catalog_dir)
    try:
        default = cat.main_problem().full_id
        sid = cat.get_idea(cat.get_session(session.session_id).seed_idea_id).canonical
    finally:
        _reset()
    # No benchmark yet -> failed problem check fires.
    assert _accept(run_tool, capsys, session.catalog_dir, session.session_id,
                   sid) == {"--allow-failed-problems"}
    # Cover (sid, params 0, default) -> passes (depth 0, no golden).
    cat = open_catalog(session.catalog_dir)
    try:
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        _cover(cat, ws, sid, default)
        cat.flush(); safety.commit()
    finally:
        _reset()
    assert _accept(run_tool, capsys, session.catalog_dir, session.session_id,
                   sid) == set()


def test_failed_problem_check_per_params_index(session, run_tool, capsys):
    """Each of a multi-params schedule's indices needs its own benchmark."""
    import json
    cat = open_catalog(session.catalog_dir)
    try:
        default = cat.main_problem().full_id
        root = [s for s in cat.schedules.values() if s.is_root()][0]
        # A fresh 2-params schedule under a new idea off the root.
        idea = cat.create_idea(root, "twop", "two params\n")
        two = cat.create_schedule(DUMMY_SOURCE, parent_idea=idea,
                                  params_text=json.dumps([{}, {"x": 1}]))
        idea.set_canonical(two.full_id)
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        _cover(cat, ws, two.full_id, default, nparams=1)  # only index 0
        cat.flush(); safety.commit()
        two_id = two.full_id
    finally:
        _reset()
    run_tool(tools.cmd_should_accept,
             ns(session=session.session_id, catalog=session.catalog_dir,
                schedule=two_id))
    out = capsys.readouterr().out
    assert "parameters index 1" in out
    assert "parameters index 0" not in out
    assert _flags(out) == {"--allow-failed-problems"}


# ---- golden checks: top-level fires, sub-session suppressed ---------------

def _catalog_two_sessions(tmp_path):
    """A catalog with root schedule R, two problems (P main, Q), a golden on R,
    a depth-0 session and a depth-1 child, both opened with that state.  Returns
    (cat_dir, parent_id, child_id, ids-dict)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule(DUMMY_SOURCE, parent_idea=None)
    idea = cat.create_idea(R, "seed", "seed\n")
    dup = cat.create_schedule(DUMMY_SOURCE, parent_idea=idea)
    idea.set_canonical(dup.full_id)
    P = cat.create_problem(["<RunGenMain>", "1"], "pp", state=ProblemState.MAIN)
    Q = cat.create_problem(["<RunGenMain>", "2"], "qq")
    cat.create_golden("golden on R\n", R.full_id)
    parent = cat.create_session(idea, None, 0)
    _init_ws(cat, parent)
    child = cat.create_session(idea, parent, 1)
    _init_ws(cat, child)
    cat.flush(); safety.commit()
    out = (cat_dir, parent.full_id, child.full_id,
           {"R": R.full_id, "dup": dup.full_id, "P": P.full_id, "Q": Q.full_id})
    _reset()
    return out


def test_last_three_checks_are_top_level_only(tmp_path, run_tool, capsys):
    """The failed-golden, deleted-problem and changed-golden checks fire for a
    top-level session but NOT for a sub-session; the failed-problem check fires
    for both (idea.md:  'Only run for top-level sessions')."""
    cat_dir, parent_id, child_id, t = _catalog_two_sessions(tmp_path)

    # Perturb shared state so all three top-level checks WOULD fire:
    # disable P (an enabled-on-opening problem) and move the golden to a
    # different node (dup), whose hlpipe we never build for the target.
    cat = open_catalog(cat_dir)
    try:
        cat.get_problem(t["P"]).set_state(ProblemState.DISABLED)
        cat.create_golden("moved golden\n", t["dup"])
        # Cover Q (still enabled) for BOTH sessions so failed-problem is isolated
        # only where we want it; here leave it UNcovered so failed_problems fires.
        cat.flush(); safety.commit()
    finally:
        _reset()

    # Target = dup (a major, non-root schedule).  Both sessions lack a benchmark
    # for Q, so failed_problems fires for both; only the top-level session adds
    # the golden/problem checks.
    parent_flags = _accept(run_tool, capsys, cat_dir, parent_id, t["dup"])
    child_flags = _accept(run_tool, capsys, cat_dir, child_id, t["dup"])

    assert "--allow-failed-problems" in parent_flags
    assert parent_flags >= {"--allow-failed-problems", "--allow-failed-golden",
                            "--allow-disabled-problems", "--allow-changed-golden"}
    # Sub-session: ONLY the failed-problem check.
    assert child_flags == {"--allow-failed-problems"}


def test_failed_golden_passes_when_hlpipe_matches(tmp_path, run_tool, capsys):
    cat_dir, parent_id, child_id, t = _catalog_two_sessions(tmp_path)
    cat = open_catalog(cat_dir)
    try:
        ws = SessionWorkspace(cat_dir, parent_id, catalog=cat)
        _cover(cat, ws, t["dup"], t["P"])   # satisfy failed-problem (P main)
        _cover(cat, ws, t["dup"], t["Q"])   # and Q
        cat.flush(); safety.commit()
    finally:
        _reset()
    # golden node is R; build MATCHING hlpipe for both R and the target dup.
    _fake_hlpipe(cat_dir, parent_id, t["R"], b"same\n")
    _fake_hlpipe(cat_dir, parent_id, t["dup"], b"same\n")
    # golden_on_opening == current golden (R), so changed-golden passes too.
    assert _accept(run_tool, capsys, cat_dir, parent_id, t["dup"]) == set()
    # Mismatched hlpipe -> failed-golden fires.
    _fake_hlpipe(cat_dir, parent_id, t["dup"], b"different\n")
    assert _accept(run_tool, capsys, cat_dir, parent_id, t["dup"]) == \
        {"--allow-failed-golden"}


# ---- close_session enforcement + overrides (run_cli) ----------------------

def _bootstrap(run_cli, tmp_path):
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "in.cpp").write_text("// gen\n")
    (tmp_path / "p.txt").write_text("explore\n")
    assert run_cli("new_catalog", "-C", cat_dir, "seed",
                   str(tmp_path / "p.txt"), str(tmp_path / "in.cpp")).returncode == 0
    r = run_cli("list_termini", "-C", cat_dir)
    handle = [l.split("handle:")[1].strip() for l in r.stdout.splitlines()
              if "handle:" in l][0]
    assert run_cli("init_workspace", "-s", handle).returncode == 0
    return cat_dir, handle


def test_should_accept_and_close_override_cli(run_cli, tmp_path):
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    # Make the seed canonical a valid output: give it commentary.
    sid = run_cli("seed_schedule_short_id", "-s", handle).stdout.strip()
    (tmp_path / "c.txt").write_text("summary\n")
    assert run_cli("comment", "-s", handle, str(tmp_path / "c.txt"),
                   sid).returncode == 0

    # should_accept reports the failed-problem check (no benchmarks) + its flag.
    r = run_cli("should_accept", "-s", handle, sid)
    assert r.returncode == 0
    assert "failed problem check" in r.stdout
    assert "--allow-failed-problems" in r.stdout

    # close_session without the override is refused (exit 1, clean).
    r = run_cli("close_session", "-s", handle, sid)
    assert r.returncode == 1
    assert "cannot close session" in r.stderr and "Traceback" not in r.stderr
    assert "--allow-failed-problems" in r.stderr

    # With the override it closes.
    r = run_cli("close_session", "-s", handle, sid, "--allow-failed-problems")
    assert r.returncode == 0, r.stderr
    assert "Closed session" in r.stdout
