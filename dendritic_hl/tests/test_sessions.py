"""Phase 4: session lifecycle + query tools, driven in-process via run_tool.

The `session` fixture supplies a depth-0 top-level session whose workspace is
consistent with its seed idea's canonical schedule (see conftest)."""

import json
import re
import os

import pytest

from dendritic_hl_lib import ids, safety, tools
from dendritic_hl_lib.errors import DhHlError
from conftest import ns, open_catalog, add_synthetic_benchmark_set


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
    assert info["output_schedules"] == []
    assert info["prompt"] == "sub-agent task\n"
    # view_session_prompt shows the prompt then the seed ideas.
    pout = _out(run_tool, capsys, tools.cmd_view_session_prompt,
                ns(catalog=session.catalog_dir, session=sub_id))
    assert "sub-agent task" in pout and "=== Seed Ideas ===" in pout
    # The seed idea's proposal text got the "Created for session" line appended.
    cat = open_catalog(session.catalog_dir)
    try:
        seed = cat.get_session(sub_id).seed_idea_id
        assert "Created for session: " + sub_id in cat.get_idea(seed).proposal_text
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()

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
    assert len(info["output_schedules"]) == 1

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
    # Seed a real private benchmark set and retag the seed idea, then close.
    cat = open_catalog(session.catalog_dir)
    try:
        from dendritic_hl_lib.context import SessionWorkspace
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        seed = cat.get_session(session.session_id).seed_idea_id
        dup = cat.get_idea(seed).canonical
        set_id = add_synthetic_benchmark_set(cat, {dup: [[100, 101, 99]]})
        ws.add_private_benchmark_set(set_id, cat)
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
        assert sess.output_benchmark_set_ids == [set_id]
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


def _minor_child_of_seed(session):
    """Create (out of band) a minor schedule -- a second, non-canonical child of
    the seed idea, which already has a canonical -- with commentary so only the
    major-schedule requirement can trip close_session.  Returns its full ID."""
    cat = open_catalog(session.catalog_dir)
    try:
        seed = cat.get_idea(cat.get_session(session.session_id).seed_idea_id)
        minor = cat.create_schedule("minor source\n", parent_idea=seed)
        minor.add_commentary("summary\n", review="neutral")
        cat.flush()
        safety.commit()
        return minor.full_id
    finally:
        _reset()


def test_close_rejects_minor_output(session, run_tool):
    """IMPL TASK (idea.md "Close Session Tool"): output schedules must be major
    schedules; a minor (non-canonical) child is refused."""
    minor_id = _minor_child_of_seed(session)
    with pytest.raises(DhHlError, match="not a major schedule"):
        run_tool(tools.cmd_close_session, session.ns(schedule=[minor_id]))


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


# ---- join_session ---------------------------------------------------------

def _build_joined_session(session, pool="jpool"):
    """Out-of-band: a closed sub-session J whose single output is a fresh
    schedule under a new seed idea, tagged *pool*, with one real benchmark set.
    Returns (J_full_id, output_parent_idea_full_id, benchmark_set_full_id)."""
    cat = open_catalog(session.catalog_dir)
    try:
        s = cat.get_session(session.session_id)
        major = next(n for n in cat.schedules.values()
                     if n.is_major() and not n.is_root())
        seed_i = cat.create_idea(major, "joinedseed", "joined\n")
        dup = cat.create_schedule("joined source\n", parent_idea=seed_i)
        seed_i.set_canonical(dup.full_id)
        bench = add_synthetic_benchmark_set(cat, {dup.full_id: [[100, 101, 99]]})
        j = cat.create_session(seed_i, s, 1, prompt="joined prompt\n")
        j.set_outputs([(dup.full_id, pool)], [bench])
        cat.flush(); safety.commit()
        return j.full_id, seed_i.full_id, bench
    finally:
        _reset()


def test_join_session(session, run_tool, capsys):
    j_id, joined_idea, bench = _build_joined_session(session)
    out = _out(run_tool, capsys, tools.cmd_join_session,
               session.ns(joined=j_id, dry_run=False, pool_prefix=""))
    assert "dh_hl: add benchmark set " + bench in out
    assert "dh_hl: add idea " + joined_idea in out
    assert "dh_hl: pool tag jpool" in out
    # The joined idea is now in S's private list at the joined pool tag.
    assert _out(run_tool, capsys, tools.cmd_get_pool_tag,
                session.ns(idea=joined_idea)).strip() == "jpool"


def test_join_session_pool_prefix(session, run_tool, capsys):
    j_id, joined_idea, _bench = _build_joined_session(session)
    _out(run_tool, capsys, tools.cmd_join_session,
         session.ns(joined=j_id, dry_run=False, pool_prefix="px"))
    assert _out(run_tool, capsys, tools.cmd_get_pool_tag,
                session.ns(idea=joined_idea)).strip() == "px.jpool"


def test_join_session_dry_run_mutates_nothing(session, run_tool, capsys):
    j_id, joined_idea, _bench = _build_joined_session(session)
    out = _out(run_tool, capsys, tools.cmd_join_session,
               session.ns(joined=j_id, dry_run=True, pool_prefix=""))
    assert "dh_hl: add idea " + joined_idea in out  # still reports
    # ...but the idea was NOT added (get_pool_tag errors).
    with pytest.raises(DhHlError, match="not in the session's private idea list"):
        run_tool(tools.cmd_get_pool_tag, session.ns(idea=joined_idea))


def test_join_session_existing_tag_unchanged(session, run_tool, capsys):
    j_id, joined_idea, _bench = _build_joined_session(session)
    run_tool(tools.cmd_join_session,
             session.ns(joined=j_id, dry_run=False, pool_prefix=""))
    # Retag locally, then join again: the existing tag must win (unchanged).
    run_tool(tools.cmd_set_pool_tag,
             session.ns(idea=joined_idea, pool_tag="mine"))
    run_tool(tools.cmd_join_session,
             session.ns(joined=j_id, dry_run=False, pool_prefix="px"))
    assert _out(run_tool, capsys, tools.cmd_get_pool_tag,
                session.ns(idea=joined_idea)).strip() == "mine"


def test_join_session_requires_outputs(session, run_tool, tmp_path, capsys):
    # A fresh sub-session of S has no outputs.
    prop = _write(tmp_path, "p.txt", "open sub\n")
    out = _out(run_tool, capsys, tools.cmd_new_sub_session,
               session.ns(proposal_name="opensub", proposal=prop))
    sub_id = _line_after(out, "Created sub-session ")
    with pytest.raises(DhHlError, match="no outputs to join"):
        run_tool(tools.cmd_join_session,
                 session.ns(joined=sub_id, dry_run=False, pool_prefix=""))


# ---- query tools: seed ideas / session commentary / output schedules ------

def test_list_seed_ideas(session, run_tool, capsys, tmp_path):
    prop = _write(tmp_path, "p.txt", "multi\n")
    majors = _major_schedule_ids(session)
    out = _out(run_tool, capsys, tools.cmd_new_sub_session,
               session.ns(proposal_name="ms", proposal=prop, schedule=majors[:2]))
    sub_id = _line_after(out, "Created sub-session ")
    listing = _out(run_tool, capsys, tools.cmd_list_seed_ideas,
                   ns(catalog=session.catalog_dir, session=sub_id))
    # Two seed ideas, both named "ms"; seed listing omits the proposal text.
    assert listing.count("ms") >= 2
    assert "Created for session:" not in listing  # omitted for seed listings


def test_view_session_commentary_and_outputs(session, run_tool, tmp_path, capsys):
    # Before closing: both tools error (no outputs).
    with pytest.raises(DhHlError, match="no output schedules"):
        run_tool(tools.cmd_view_session_commentary, session.ns())
    with pytest.raises(DhHlError, match="no output schedules"):
        run_tool(tools.cmd_list_output_schedules, session.ns())

    _comment(session, run_tool, tmp_path)
    run_tool(tools.cmd_close_session, session.ns())

    vc = _out(run_tool, capsys, tools.cmd_view_session_commentary, session.ns())
    assert "OUTPUT SCHEDULE:" in vc and "session summary" in vc
    lo = _out(run_tool, capsys, tools.cmd_list_output_schedules, session.ns())
    assert "Schedule:" in lo


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


# ---- "test" that stops infinite buildup of garbage handles ------------------------

def test_delete_pytest_handles():
    """It would be nice if this didn't exist.

    If you change the sessions code in ways that break this "test",
    consider tracking down the cause of this leakage and eliminate
    the need for this foot-gun-y test.
    Remind me that I suggested doing this.
    """
    from dendritic_hl_lib import locks
    pattern = re.compile(".*/pytest-of-.*/pytest-[0-9].*")
    prefixes = ("/tmp", "/private")
    handles_dir = locks.handles_dir()
    for fname in os.listdir(handles_dir):
        try:
            catalog_dir_abspath, _ = locks.resolve_handle(fname)
        except DhHlError:
            continue
        print(catalog_dir_abspath)
        if pattern.match(catalog_dir_abspath) and any(
                catalog_dir_abspath.startswith(x) for x in prefixes
        ):
            os.remove(os.path.join(handles_dir, fname))
