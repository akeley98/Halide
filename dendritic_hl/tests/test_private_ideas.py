"""Session private idea list (now a pool-tag JSON object), the pool-tag tools
(get/set/hide/rename), new_idea's pool-tag rules, restore_idea, catalog_location,
and the enriched idea listing.

Driven in-process via run_tool against the `session` fixture (see conftest).

Per idea.md (New Sessions batch), the private idea list is unordered {idea ->
pool tag}; the cost-ranked `list_private_ideas*` view is a future task, so those
tools are exercised only lightly here (skipped) and the list is tested through
the pool-tag tools instead.
"""

import os

import pytest

from dendritic_hl_lib import safety, tools
from dendritic_hl_lib.errors import DhHlError
from conftest import ns, open_catalog, make_catalog_session, Sess


def _out(run_tool, capsys, fn, args):
    capsys.readouterr()
    run_tool(fn, args)
    return capsys.readouterr().out


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _make_idea(session, run_tool, tmp_path, name, text=None, pool_tag=None):
    prop = _write(tmp_path, name + ".txt", text or (name + " proposal\n"))
    run_tool(tools.cmd_new_idea,
             session.ns(proposal_name=name, proposal=prop, pool_tag=pool_tag))


def _set_canonical(session, idea_short):
    """Give an idea a canonical schedule out-of-band (canon needs a real build)."""
    cat = open_catalog(session.catalog_dir)
    try:
        idea = cat.resolve_idea(idea_short)
        some_sched = next(iter(cat.schedules.values()))
        idea.set_canonical(some_sched.full_id)
        cat.flush()
        safety.commit()
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()
        safety._new_entries.clear()
        safety._pending_overwrites.clear()


# ---- new_idea + pool tags -------------------------------------------------

def test_new_idea_inherits_parent_pool_tag(session, run_tool, tmp_path, capsys):
    # The seed idea is in the private list as "default"; a new_idea on its
    # canonical (default schedule) inherits that tag when --pool-tag is omitted.
    _make_idea(session, run_tool, tmp_path, "child")
    tag = _out(run_tool, capsys, tools.cmd_get_pool_tag,
               session.ns(idea=".child")).strip()
    assert tag == "default"


def test_new_idea_explicit_pool_tag(session, run_tool, tmp_path, capsys):
    _make_idea(session, run_tool, tmp_path, "child", pool_tag="mypool")
    tag = _out(run_tool, capsys, tools.cmd_get_pool_tag,
               session.ns(idea=".child")).strip()
    assert tag == "mypool"


def test_new_idea_on_root_requires_pool_tag(session, run_tool, tmp_path):
    # A fresh root has no parent idea to inherit from.
    session.write_workspace("root source\n")
    run_tool(tools.cmd_new_root, session.ns())
    prop = _write(tmp_path, "p.txt", "idea under a root\n")
    with pytest.raises(DhHlError, match="pool-tag is required"):
        run_tool(tools.cmd_new_idea,
                 session.ns(proposal_name="orphan", proposal=prop))


def test_new_idea_parent_not_in_list_requires_pool_tag(session, run_tool,
                                                       tmp_path):
    """If the parent idea isn't in the private list, its tag can't be inherited.
    Drop the seed idea from the list, then new_idea on its canonical (the
    default schedule) has a parent idea that's absent -> --pool-tag required."""
    cat = open_catalog(session.catalog_dir)
    try:
        ws = _ws(cat, session)
        seed = cat.get_session(session.session_id).seed_idea_id
        ws.remove_private_idea(seed)
        cat.flush(); safety.commit()
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()
        safety._new_entries.clear(); safety._pending_overwrites.clear()
    prop = _write(tmp_path, "g.txt", "child of an absent-parent idea\n")
    with pytest.raises(DhHlError, match="pool-tag is required"):
        run_tool(tools.cmd_new_idea,
                 session.ns(proposal_name="orphan2", proposal=prop))


def _ws(cat, session):
    from dendritic_hl_lib.context import SessionWorkspace
    return SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)


# ---- pool-tag tools -------------------------------------------------------

def test_get_pool_tag_absent_errors(session, run_tool, tmp_path):
    # A brand-new idea created out-of-band isn't in the private list.
    cat = open_catalog(session.catalog_dir)
    try:
        seed = cat.get_session(session.session_id).seed_idea_id
        sched = cat.get_idea(seed).canonical
        idea = cat.create_idea(cat.get_schedule(sched), "loner", "x\n")
        loner = idea.full_id
        cat.flush(); safety.commit()
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()
        safety._new_entries.clear(); safety._pending_overwrites.clear()
    with pytest.raises(DhHlError, match="not in the session's private idea list"):
        run_tool(tools.cmd_get_pool_tag, session.ns(idea=loner))


def test_set_and_hide_pool_tag(session, run_tool, tmp_path, capsys):
    _make_idea(session, run_tool, tmp_path, "x", pool_tag="a")
    run_tool(tools.cmd_set_pool_tag, session.ns(idea=".x", pool_tag="b"))
    assert _out(run_tool, capsys, tools.cmd_get_pool_tag,
                session.ns(idea=".x")).strip() == "b"
    run_tool(tools.cmd_hide_private_idea, session.ns(idea=".x"))
    assert _out(run_tool, capsys, tools.cmd_get_pool_tag,
                session.ns(idea=".x")).strip() == ".b"


def test_rename_pool_tag(session, run_tool, tmp_path, capsys):
    _make_idea(session, run_tool, tmp_path, "one", pool_tag="grp")
    _make_idea(session, run_tool, tmp_path, "two", pool_tag="grp")
    _make_idea(session, run_tool, tmp_path, "three", pool_tag="other")
    out = _out(run_tool, capsys, tools.cmd_rename_pool_tag,
               session.ns(pool_tag_before="grp", pool_tag_after="grp2"))
    assert "2 idea nodes updated" in out
    assert _out(run_tool, capsys, tools.cmd_get_pool_tag,
                session.ns(idea=".one")).strip() == "grp2"
    assert _out(run_tool, capsys, tools.cmd_get_pool_tag,
                session.ns(idea=".three")).strip() == "other"


# ---- multiple sessions: private lists must not bleed ----------------------

def test_private_lists_are_per_session(tmp_path, run_tool, capsys):
    """Two sessions in the same catalog keep independent private idea lists."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    c1, s1 = make_catalog_session(cat_dir)
    S1 = Sess(c1, s1)
    # A second, independent session on the same catalog.
    c2, s2 = make_catalog_session(str(tmp_path / "proj2.dh_hl"))
    S2 = Sess(c2, s2)

    run_tool(tools.cmd_set_pool_tag, S1.ns(idea=".seed", pool_tag="s1only"))
    # S2's list has its own seed at "default", untouched by S1's change.
    assert _out(run_tool, capsys, tools.cmd_get_pool_tag,
                S2.ns(idea=".seed")).strip() == "default"
    assert _out(run_tool, capsys, tools.cmd_get_pool_tag,
                S1.ns(idea=".seed")).strip() == "s1only"


# The cost-ranked list_private_ideas frontier is covered in
# test_list_private_ideas.py.


# ---- restore_idea ---------------------------------------------------------

def test_restore_idea_loads_parent_schedule(session, run_tool, tmp_path, capsys):
    from conftest import DUMMY_SOURCE
    _make_idea(session, run_tool, tmp_path, "impl_me")
    session.write_workspace("garbage\n")
    out = _out(run_tool, capsys, tools.cmd_restore_idea, session.ns(idea=".impl_me"))
    assert open(session.workspace_path, encoding="utf-8").read() == DUMMY_SOURCE
    assert "ready to implement" in out
    run_tool(tools.cmd_status, session.ns())
    assert "current idea:" in capsys.readouterr().out.lower()


def test_restore_idea_warns_if_canonical_exists(session, run_tool, tmp_path,
                                                capsys):
    _make_idea(session, run_tool, tmp_path, "already_done")
    _set_canonical(session, ".already_done")
    out = _out(run_tool, capsys, tools.cmd_restore_idea,
               session.ns(idea=".already_done"))
    assert "WARNING" in out
    assert "restore_schedule" in out


# ---- session creation tags the idea in the PARENT session's list ----------

def test_sub_session_idea_lands_in_parent_private_list(session, run_tool,
                                                       tmp_path, capsys):
    prop = _write(tmp_path, "sub.txt", "sub agent job\n")
    run_tool(tools.cmd_new_sub_session,
             session.ns(proposal_name="subtask", proposal=prop))
    # The seed idea created for the sub-session is in the PARENT's list, tagged
    # session.{proposal name}.
    tag = _out(run_tool, capsys, tools.cmd_get_pool_tag,
               session.ns(idea=".subtask")).strip()
    assert tag == "session.subtask"


# ---- catalog_location -----------------------------------------------------

def test_catalog_location_by_catalog(session, run_tool, capsys):
    out = _out(run_tool, capsys, tools.cmd_catalog_location,
               ns(catalog=session.catalog_dir))
    assert out.strip() == os.path.abspath(session.catalog_dir)


def test_catalog_location_by_handle(session, run_tool, capsys):
    handle = _out(run_tool, capsys, tools.cmd_session_handle, session.ns()).strip()
    out = _out(run_tool, capsys, tools.cmd_catalog_location, ns(session=handle))
    assert out.strip() == os.path.abspath(session.catalog_dir)


# ---- enriched list_ideas: canonical line ----------------------------------

def test_list_ideas_shows_canonical_state(session, run_tool, tmp_path, capsys):
    _make_idea(session, run_tool, tmp_path, "nocanon")
    out = _out(run_tool, capsys, tools.cmd_list_child_ideas, session.ns())
    assert "nocanon" in out
    assert "(none)" in out  # its canonical line
