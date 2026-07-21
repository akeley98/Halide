"""Session private idea list (new_idea/session-creation append, list_private_*,
forget_private_idea), restore_idea, catalog_location, and the enriched idea
listing (canonical line + Created-for-session line) shared by list_ideas.

Driven in-process via run_tool against the `session` fixture (see conftest)."""

import os

import pytest

from dendritic_hl_lib import safety, tools
from dendritic_hl_lib.errors import DhHlError
from conftest import ns, open_catalog


def _out(run_tool, capsys, fn, args):
    capsys.readouterr()
    run_tool(fn, args)
    return capsys.readouterr().out


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _make_idea(session, run_tool, tmp_path, name, text=None):
    prop = _write(tmp_path, name + ".txt", text or (name + " proposal\n"))
    run_tool(tools.cmd_new_idea, session.ns(proposal_name=name, proposal=prop))


def _set_canonical(session, idea_short):
    """Give an idea a canonical schedule out-of-band (canon needs a real build).
    The filter tools only check canonical presence, so any schedule ID works."""
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


# ---- new_idea appends to the private idea list ----------------------------

def test_new_idea_adds_to_private_list_most_recent_first(session, run_tool,
                                                         tmp_path, capsys):
    _make_idea(session, run_tool, tmp_path, "idea1")
    _make_idea(session, run_tool, tmp_path, "idea2")

    out = _out(run_tool, capsys, tools.cmd_list_private_ideas, session.ns())
    assert "idea1" in out and "idea2" in out
    # Most recent first: idea2 (added later) appears before idea1.
    assert out.index("idea2") < out.index("idea1")


def test_empty_private_list(session, run_tool, capsys):
    out = _out(run_tool, capsys, tools.cmd_list_private_ideas, session.ns())
    assert "(no private ideas)" in out


# ---- forget_private_idea --------------------------------------------------

def test_forget_private_idea(session, run_tool, tmp_path, capsys):
    _make_idea(session, run_tool, tmp_path, "gone")
    run_tool(tools.cmd_forget_private_idea, session.ns(idea=".gone"))
    out = _out(run_tool, capsys, tools.cmd_list_private_ideas, session.ns())
    assert "gone" not in out


def test_forget_absent_idea_errors(session, run_tool, tmp_path):
    _make_idea(session, run_tool, tmp_path, "here")
    run_tool(tools.cmd_forget_private_idea, session.ns(idea=".here"))
    with pytest.raises(DhHlError, match="not in the session's private idea list"):
        run_tool(tools.cmd_forget_private_idea, session.ns(idea=".here"))


# ---- todo / done filtering + N limit --------------------------------------

def test_todo_done_split(session, run_tool, tmp_path, capsys):
    _make_idea(session, run_tool, tmp_path, "todoidea")
    _make_idea(session, run_tool, tmp_path, "doneidea")
    _set_canonical(session, ".doneidea")

    todo = _out(run_tool, capsys, tools.cmd_list_private_ideas_todo, session.ns())
    assert "todoidea" in todo and "doneidea" not in todo

    done = _out(run_tool, capsys, tools.cmd_list_private_ideas_done, session.ns())
    assert "doneidea" in done and "todoidea" not in done


def test_n_limit_counts_only_printed(session, run_tool, tmp_path, capsys):
    # Order added: t1(todo), d1(done), t2(todo).  todo list most-recent-first is
    # [t2, t1]; N=1 keeps only t2.  The excluded d1 must not consume the budget.
    _make_idea(session, run_tool, tmp_path, "t1")
    _make_idea(session, run_tool, tmp_path, "d1")
    _set_canonical(session, ".d1")
    _make_idea(session, run_tool, tmp_path, "t2")

    out = _out(run_tool, capsys, tools.cmd_list_private_ideas_todo,
               session.ns(n=1))
    assert "t2" in out and "t1" not in out


# ---- restore_idea ---------------------------------------------------------

def test_restore_idea_loads_parent_schedule(session, run_tool, tmp_path, capsys):
    from conftest import DUMMY_SOURCE
    _make_idea(session, run_tool, tmp_path, "impl_me")
    # Scribble on the workspace, then restore_idea should reset it to the idea's
    # parent schedule source (the seed canonical == DUMMY_SOURCE).
    session.write_workspace("garbage\n")
    out = _out(run_tool, capsys, tools.cmd_restore_idea, session.ns(idea=".impl_me"))
    assert open(session.workspace_path, encoding="utf-8").read() == DUMMY_SOURCE
    assert "ready to implement" in out
    # Current idea state now points at the idea.
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


# ---- session creation appends to the PARENT session's list ----------------

def test_sub_session_idea_lands_in_parent_private_list(session, run_tool,
                                                       tmp_path, capsys):
    prop = _write(tmp_path, "sub.txt", "sub agent job\n")
    run_tool(tools.cmd_new_sub_session,
             session.ns(proposal_name="subtask", proposal=prop))
    # The seed idea created for the sub-session is now in the PARENT's list.
    out = _out(run_tool, capsys, tools.cmd_list_private_ideas, session.ns())
    assert "subtask" in out
    # And it shows the "Created for session:" line from the enriched listing.
    assert "Created for session:" in out


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
    out = _out(run_tool, capsys, tools.cmd_list_ideas, session.ns())
    assert "nocanon" in out
    assert "(none)" in out  # its canonical line
