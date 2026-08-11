"""The golden open/close state matrix for the top-level `should_accept` /
`close_session` golden checks (idea.md "Changed Golden Check" / "Failed Golden
Check").

Four quadrants of (golden schedule node on OPEN) x (golden schedule node on
CLOSE), holding the other checks satisfied (problem covered; algorithm hlpipes
equal, so the *failed*-golden check always passes and only the golden-IDENTITY
change is under test):

                       | no golden on close | golden on close (!= open)
    no golden on open  |  A1: error-free    |  A2: error-free
    golden on open     |  B1: changed-golden|  B2: changed-golden

Only B1/B2 error -- the *changed*-golden check is guarded by "golden on opening
exists", so a session that never had a golden (A1) or that established one during
the session (A2, the normal workflow) closes cleanly.  There is deliberately NO
"must have a golden to close" requirement (idea.md: a golden is intentionally not
added by default), so A1 is intentionally error-free.  Confirmed behavior.
"""

import os

from dendritic_hl_lib import build, locks, safety, tools
from dendritic_hl_lib.context import SessionWorkspace
from conftest import DUMMY_SOURCE, add_synthetic_benchmark_set, ns, open_catalog


def _reset():
    locks._reset_for_tests()


def _hlpipe(cat_dir, sess_id, sched_id, content=b"ALGO\n"):
    rel = build._build_output_rel(sched_id, "algorithm_hlpipe", 0)
    p = os.path.join(cat_dir, "private", sess_id, "bin", rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(content)


def _build_quad(tmp_path, name, golden_on_open, close):
    """A top-level session whose output O has its problem covered and whose
    algorithm hlpipe equals every golden target's, so only the golden open/close
    IDENTITY varies.  *golden_on_open*: create a golden on O before the session.
    *close*: "none" (leave/clear the golden), "sameO" (golden on O), or "diff"
    (golden on a different node G2)."""
    cat_dir = str(tmp_path / (name + ".dh_hl"))
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule(DUMMY_SOURCE, parent_idea=None)
    seed = cat.create_idea(R, "seed", "seed\n")
    O = cat.create_schedule(DUMMY_SOURCE, parent_idea=seed)
    seed.set_canonical(O.full_id)
    alt = cat.create_idea(R, "alt", "alt\n")
    G2 = cat.create_schedule(DUMMY_SOURCE, parent_idea=alt)
    alt.set_canonical(G2.full_id)
    P = cat.create_problem(["<RunGenMain>", "1"], "pp", state="main")
    if golden_on_open:
        cat.create_golden("golden on open = O\n", O.full_id)
    sess = cat.create_session(seed, None, 0)   # snapshots golden-on-opening
    ws = SessionWorkspace(cat_dir, sess.full_id, catalog=cat)
    ws.initialize(DUMMY_SOURCE, ("idea", seed.full_id))
    ws.set_pool_tag(seed.full_id, "default")
    bs = add_synthetic_benchmark_set(cat, {O.full_id: [[1.0]]}, problem=P.full_id)
    ws.add_private_benchmark_set(bs, catalog=cat)
    # Current golden at close time (a later golden overrides an earlier one).
    if close == "none" and golden_on_open:
        cat.create_golden("cleared to none\n", None)
    elif close == "sameO":
        cat.create_golden("golden on close = O\n", O.full_id)
    elif close == "diff":
        cat.create_golden("golden on close = G2\n", G2.full_id)
    cat.flush(); safety.commit()
    sid = sess.full_id
    _reset()
    # Equal algorithm hlpipes for O and G2 -> failed-golden never fires on a
    # mismatch, isolating the changed-golden (identity) dimension.
    _hlpipe(cat_dir, sid, O.full_id)
    _hlpipe(cat_dir, sid, G2.full_id)
    return cat_dir, sid, O.full_id


def _accept_flags(run_tool, capsys, cat_dir, sid, oid):
    run_tool(tools.cmd_should_accept, ns(session=sid, catalog=cat_dir, schedule=oid))
    out = capsys.readouterr().out
    for line in out.splitlines():
        if line.startswith("Overrides needed"):
            return set(line.split(":", 1)[1].split())
    assert "All checks passed" in out, out
    return set()


def test_golden_open_close_matrix(tmp_path, run_tool, capsys):
    cases = {
        "A1_noopen_noclose":  (False, "none",  set()),
        "A2_noopen_hasclose": (False, "sameO", set()),
        "B1_open_noclose":    (True,  "none",  {"--allow-changed-golden"}),
        "B2_open_diffclose":  (True,  "diff",  {"--allow-changed-golden"}),
    }
    for name, (on_open, close, expected) in cases.items():
        cat_dir, sid, oid = _build_quad(tmp_path, name, on_open, close)
        got = _accept_flags(run_tool, capsys, cat_dir, sid, oid)
        assert got == expected, "{}: expected {}, got {}".format(name, expected, got)
        _reset()


def test_close_session_blocks_changed_golden_until_overridden(tmp_path, run_tool):
    """B1/B2 also block close_session, and --allow-changed-golden forces it."""
    cat_dir, sid, oid = _build_quad(tmp_path, "b2_close", True, "diff")
    # Output needs commentary to be a valid close output.
    cat = open_catalog(cat_dir)
    try:
        cat.get_schedule(oid).add_commentary("done\n", review="neutral")
        cat.flush(); safety.commit()
    finally:
        _reset()
    import pytest
    from dendritic_hl_lib.errors import DhHlError
    with pytest.raises(DhHlError, match="changed golden check"):
        run_tool(tools.cmd_close_session, ns(session=sid, catalog=cat_dir,
                                             schedule=[oid]))
    _reset()
    run_tool(tools.cmd_close_session,
             ns(session=sid, catalog=cat_dir, schedule=[oid],
                allow_changed_golden=True))
    cat = open_catalog(cat_dir)
    try:
        assert cat.get_session(sid).has_outputs()
    finally:
        _reset()
