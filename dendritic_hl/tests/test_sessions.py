"""Phase 4: session lifecycle + query tools, driven in-process via run_tool.

The `session` fixture supplies a depth-0 top-level session whose workspace is
consistent with its seed idea's canonical schedule (see conftest)."""

import json
import os

import pytest

from dendritic_hl_lib import ids, safety, tools
from dendritic_hl_lib.errors import DhHlError
from conftest import ns, open_catalog


def _out(run_tool, capsys, fn, args):
    capsys.readouterr()
    run_tool(fn, args)
    return capsys.readouterr().out


def _line_after(out, prefix):
    for line in out.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise AssertionError("no line starting {!r} in:\n{}".format(prefix, out))


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


# ---- new_catalog ----------------------------------------------------------

def test_new_catalog_creates_everything(tmp_path, run_tool, capsys):
    cat_dir = str(tmp_path / "fresh.dh_hl")
    prop = _write(tmp_path, "p.txt", "explore tiling\n")
    inp = _write(tmp_path, "in.cpp", "// generator source\n")
    out = _out(run_tool, capsys, tools.cmd_new_catalog,
               ns(catalog=cat_dir, proposal_name="seed", proposal=prop, input_cpp=inp))
    assert "Created catalog" in out
    sid = _line_after(out, "Session: ")
    assert sid.startswith("0_")  # depth-0 top-level

    # Skeleton on disk: two schedules (root + canonical dup), one idea, one session.
    assert len(os.listdir(os.path.join(cat_dir, "sch"))) == 2
    assert len(os.listdir(os.path.join(cat_dir, "idea"))) == 1
    assert os.listdir(os.path.join(cat_dir, "session")) == [sid]

    # The private workspace is NOT initialized by new_catalog (idea.md); status
    # reports missing workspace files until init_workspace runs.
    st = _out(run_tool, capsys, tools.cmd_status, ns(catalog=cat_dir, session=sid))
    assert "missing workspace" in st

    run_tool(tools.cmd_init_workspace, ns(catalog=cat_dir, session=sid, force=False))
    ws = os.path.join(cat_dir, "private", sid, "generator.cpp")
    assert open(ws).read() == "// generator source\n"
    st = _out(run_tool, capsys, tools.cmd_status, ns(catalog=cat_dir, session=sid))
    assert "workspace consistent" in st


def test_new_catalog_rejects_existing_dir(session, run_tool, tmp_path):
    prop = _write(tmp_path, "p.txt", "x\n")
    inp = _write(tmp_path, "in.cpp", "y\n")
    with pytest.raises(DhHlError, match="already exists"):
        run_tool(tools.cmd_new_catalog,
                 ns(catalog=session.catalog_dir, proposal_name="seed",
                    proposal=prop, input_cpp=inp))


# ---- sub-sessions ---------------------------------------------------------

def test_new_sub_session(session, run_tool, capsys, tmp_path):
    prop = _write(tmp_path, "p.txt", "sub-agent task\n")
    out = _out(run_tool, capsys, tools.cmd_new_sub_session,
               session.ns(proposal_name="subtask", proposal=prop))
    sub_id = _line_after(out, "Created sub-session ")
    assert sub_id.startswith("1_")  # depth+1

    info = json.loads(_out(run_tool, capsys, tools.cmd_json_session_info,
                           ns(catalog=session.catalog_dir, session=sub_id)))
    assert info["depth"] == 1
    assert info["parent"] == session.session_id
    assert info["output_schedule"] is None
    # The proposal text got the "Created for session" line appended.
    iout = _out(run_tool, capsys, tools.cmd_view_session_idea,
                ns(catalog=session.catalog_dir, session=sub_id))
    assert "sub-agent task" in iout
    assert "Created for session: " + sub_id in iout

    # The parent now lists the sub as a child.
    pinfo = json.loads(_out(run_tool, capsys, tools.cmd_json_session_info,
                            session.ns()))
    assert sub_id in pinfo["children"]


def _major_schedule_ids(session):
    cat = open_catalog(session.catalog_dir)
    try:
        return [s.full_id for s in cat.schedules.values() if s.is_major()]
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()


def test_new_sub_session_multiple_parents(session, run_tool, capsys, tmp_path):
    majors = _major_schedule_ids(session)  # root + seed canonical
    assert len(majors) >= 2
    prop = _write(tmp_path, "p.txt", "two parents\n")
    out = _out(run_tool, capsys, tools.cmd_new_sub_session,
               session.ns(proposal_name="multi", proposal=prop,
                          schedule=majors[:2]))
    sub_id = _line_after(out, "Created sub-session ")
    # The sub-session has one seed idea per parent schedule.
    cat = open_catalog(session.catalog_dir)
    try:
        assert len(cat.get_session(sub_id).seed_idea_ids) == 2
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()


def test_new_sub_session_empty_list_uses_default(session, run_tool, capsys,
                                                 tmp_path):
    prop = _write(tmp_path, "p.txt", "default parent\n")
    out = _out(run_tool, capsys, tools.cmd_new_sub_session,
               session.ns(proposal_name="deflt", proposal=prop, schedule=[]))
    sub_id = _line_after(out, "Created sub-session ")
    cat = open_catalog(session.catalog_dir)
    try:
        assert len(cat.get_session(sub_id).seed_idea_ids) == 1
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()


# ---- close / successor / delist ------------------------------------------

def _comment(session, run_tool, tmp_path, review="neutral",
             text="session summary\n"):
    cfile = _write(tmp_path, "c.txt", text)
    run_tool(tools.cmd_comment, session.ns(commentary=cfile, review=review))


def test_close_session_requires_commentary(session, run_tool):
    with pytest.raises(DhHlError, match="no commentary"):
        run_tool(tools.cmd_close_session, session.ns())


def test_close_then_successor(session, run_tool, capsys, tmp_path):
    _comment(session, run_tool, tmp_path)
    out = _out(run_tool, capsys, tools.cmd_close_session, session.ns())
    assert "Closed session" in out

    info = json.loads(_out(run_tool, capsys, tools.cmd_json_session_info,
                           session.ns()))
    assert info["output_schedule"] is not None

    # Closing again is refused.
    with pytest.raises(DhHlError, match="already has outputs"):
        run_tool(tools.cmd_close_session, session.ns())

    # A closed depth-0 session is still the terminus (closed terminus is normal),
    # but no longer "open".
    termini = _out(run_tool, capsys, tools.cmd_list_termini,
                   ns(catalog=session.catalog_dir))
    assert session.session_id in termini
    opens = _out(run_tool, capsys, tools.cmd_list_open_sessions,
                 ns(catalog=session.catalog_dir))
    assert session.session_id not in opens

    # Now a successor can start.
    prop = _write(tmp_path, "succ.txt", "next round\n")
    sout = _out(run_tool, capsys, tools.cmd_new_successor_session,
                session.ns(proposal_name="round2", proposal=prop))
    succ_id = _line_after(sout, "Created successor session ")
    assert succ_id.startswith("0_")  # successor is also top-level

    # The original is no longer a terminus; the successor is.
    termini = _out(run_tool, capsys, tools.cmd_list_termini,
                   ns(catalog=session.catalog_dir))
    assert session.session_id not in termini
    assert succ_id in termini


def _reset():
    from dendritic_hl_lib import locks
    locks._reset_for_tests()
    safety._new_entries.clear()
    safety._pending_overwrites.clear()


def test_close_records_pool_tag_and_benchmark_sets(session, run_tool, tmp_path,
                                                   capsys):
    # Seed a private benchmark set and retag the seed idea, then close.
    cat = open_catalog(session.catalog_dir)
    try:
        from dendritic_hl_lib.context import SessionWorkspace
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        ws.add_private_benchmark_set("Testhost_2026-01-01T000000_000000Z")
        seed = cat.get_session(session.session_id).seed_idea_id
        ws.set_pool_tag(seed, "chosen")
        cat.flush(); safety.commit()
    finally:
        _reset()
    _comment(session, run_tool, tmp_path)
    run_tool(tools.cmd_close_session, session.ns())
    cat = open_catalog(session.catalog_dir)
    try:
        sess = cat.get_session(session.session_id)
        assert list(sess.output_schedule_pool_tags().values()) == ["chosen"]
        assert sess.output_benchmark_set_ids == [
            "Testhost_2026-01-01T000000_000000Z"]
    finally:
        _reset()


def test_close_rejects_root_output(session, run_tool, tmp_path):
    _comment(session, run_tool, tmp_path)
    root_id = [s for s in _major_schedule_ids(session)
               if _is_root(session, s)][0]
    with pytest.raises(DhHlError, match="cannot be a root node"):
        run_tool(tools.cmd_close_session, session.ns(schedule=[root_id]))


def _is_root(session, sched_id):
    cat = open_catalog(session.catalog_dir)
    try:
        return cat.get_schedule(sched_id).is_root()
    finally:
        _reset()


def test_close_rejects_parent_not_in_list(session, run_tool, tmp_path):
    _comment(session, run_tool, tmp_path)
    cat = open_catalog(session.catalog_dir)
    try:
        from dendritic_hl_lib.context import SessionWorkspace
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        ws.remove_private_idea(cat.get_session(session.session_id).seed_idea_id)
        cat.flush(); safety.commit()
    finally:
        _reset()
    with pytest.raises(DhHlError, match="not in this session's private idea list"):
        run_tool(tools.cmd_close_session, session.ns())


# ---- root_of / session_root_of --------------------------------------------

def test_root_of(session, run_tool, capsys):
    out = _out(run_tool, capsys, tools.cmd_root_of, session.ns())
    root_id = [s for s in _major_schedule_ids(session) if _is_root(session, s)][0]
    # root_of prints a (short) ID resolving to the tree root.
    cat = open_catalog(session.catalog_dir)
    try:
        assert cat.resolve_schedule(out.strip()).full_id == root_id
    finally:
        _reset()


def test_session_root_of_finds_seed_child(session, run_tool, capsys):
    # The default schedule (seed canonical) is itself a child of the seed idea.
    out = _out(run_tool, capsys, tools.cmd_session_root_of, session.ns())
    assert out.strip()  # resolves to a schedule (the seed canonical)


def test_session_root_of_fails_off_subtree(session, run_tool):
    root_id = [s for s in _major_schedule_ids(session) if _is_root(session, s)][0]
    with pytest.raises(DhHlError, match="no ancestor schedule"):
        run_tool(tools.cmd_session_root_of, session.ns(schedule=root_id))


def test_successor_requires_self_closed(session, run_tool, tmp_path):
    prop = _write(tmp_path, "s.txt", "x\n")
    with pytest.raises(DhHlError, match="self-closed"):
        run_tool(tools.cmd_new_successor_session,
                 session.ns(proposal_name="r2", proposal=prop))


def test_delist_session(session, run_tool, capsys):
    run_tool(tools.cmd_delist_session, session.ns())
    info = json.loads(_out(run_tool, capsys, tools.cmd_json_session_info,
                           session.ns()))
    assert info["delisted"] is True
    # Delisted -> not a terminus, and closed (not open).
    termini = _out(run_tool, capsys, tools.cmd_list_termini,
                   ns(catalog=session.catalog_dir))
    assert session.session_id not in termini


# ---- copy / id-of / workspace / views ------------------------------------

def test_copy_and_id_getters(session, run_tool, capsys, tmp_path):
    # seed-schedule getters (the seed idea's canonical, == the consistent dup).
    seed_full = _out(run_tool, capsys, tools.cmd_seed_schedule_full_id,
                     session.ns()).strip()
    assert len(seed_full) == 90  # a schedule full ID

    dest = str(tmp_path / "copied.cpp")
    run_tool(tools.cmd_copy_seed_schedule,
             session.ns(output=dest))
    from conftest import DUMMY_SOURCE
    assert open(dest).read() == DUMMY_SOURCE

    # workspace path getters point into private/{id}.
    wpath = _out(run_tool, capsys, tools.cmd_workspace_schedule,
                 session.ns()).strip()
    assert wpath.endswith(os.path.join("private", session.session_id, "generator.cpp"))
    bpath = _out(run_tool, capsys, tools.cmd_workspace_bin, session.ns()).strip()
    assert bpath.endswith(os.path.join("private", session.session_id, "bin"))

    # session identity getters.
    assert _out(run_tool, capsys, tools.cmd_session_full_id,
                session.ns()).strip() == session.session_id
    handle = _out(run_tool, capsys, tools.cmd_session_handle,
                  session.ns()).strip()
    assert handle.startswith("tmp.")


def test_terminus_and_output_getters_after_close(session, run_tool, capsys, tmp_path):
    _comment(session, run_tool, tmp_path)
    run_tool(tools.cmd_close_session, session.ns())

    out_full = _out(run_tool, capsys, tools.cmd_session_output_full_id,
                    session.ns()).strip()
    term_full = _out(run_tool, capsys, tools.cmd_terminus_schedule_full_id,
                     ns(catalog=session.catalog_dir)).strip()
    # The unique terminus's output is this session's output.
    assert out_full == term_full


def test_view_commentary(session, run_tool, capsys, tmp_path):
    _comment(session, run_tool, tmp_path, review="positive")
    out = _out(run_tool, capsys, tools.cmd_view_all_commentary, session.ns())
    assert "review: positive" in out
    assert "cancelled: false" in out
    assert "session summary" in out


def test_json_export_has_all_categories(session, run_tool, capsys):
    obj = json.loads(_out(run_tool, capsys, tools.cmd_json_export,
                          ns(catalog=session.catalog_dir)))
    assert set(obj) == {"ideas", "schedules", "sessions", "benchmark_sets"}
    assert session.session_id in obj["sessions"]
    assert len(obj["schedules"]) == 2  # root + canonical dup
    assert len(obj["ideas"]) == 1


# ---- session_is_closed loop guard (cooked catalog) ------------------------

def _write_session_dir(cat_dir, sid, seed_idea, parent=None, delisted=False):
    d = os.path.join(cat_dir, "session", sid)
    os.makedirs(d)
    with open(os.path.join(d, "seed_idea.txt"), "w") as f:
        f.write(seed_idea + "\n")
    if parent is not None:
        with open(os.path.join(d, "parent.txt"), "w") as f:
            f.write(parent + "\n")
    if delisted:
        open(os.path.join(d, "delisted.txt"), "w").close()


def test_session_is_closed_raises_on_bad_parent_edge(tmp_path):
    """A sub-session whose parent is NOT strictly older (a tree-invariant
    violation on a cooked catalog) makes session_is_closed RAISE, rather than
    silently absorbing the corruption -- this both terminates the walk and
    surfaces the problem (idea.md loop-guard policy)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("r", parent_idea=None)
    I = cat.create_idea(R, "seed", "x\n")
    cat.flush()
    safety.commit()

    # Craft a depth-0 (self-closed) parent that is NEWER than its depth-1
    # "child" -- i.e. the parent-older invariant is violated.
    parent_id = ids.make_session_id(0, "2026-07-20T120000_000000Z", "u", "h")
    child_id = ids.make_session_id(1, "2026-07-20T110000_000000Z", "u", "h")
    _write_session_dir(cat_dir, parent_id, I.full_id, delisted=True)
    _write_session_dir(cat_dir, child_id, I.full_id, parent=parent_id)

    cat2 = open_catalog(cat_dir)  # fresh view
    # The parent itself is fine (self-closed, no parent to walk to).
    assert cat2.session_is_closed(cat2.get_session(parent_id)) is True
    # Walking up from the child hits the invalid edge -> raise.
    with pytest.raises(DhHlError, match="not older than its sub-session"):
        cat2.session_is_closed(cat2.get_session(child_id))
