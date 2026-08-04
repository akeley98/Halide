"""init_workspace (+ --force) and the current-anchor tools.

Driven in-process via run_tool against a catalog whose private workspace is NOT
pre-initialized, so we can exercise init_workspace from scratch.
"""

import json
import os

import pytest

from dendritic_hl_lib import locks, safety, tools
from dendritic_hl_lib.catalog import Catalog
from dendritic_hl_lib.context import SessionWorkspace
from dendritic_hl_lib.errors import DhHlError
from conftest import DUMMY_SOURCE, Sess, ns


def _bare_session(tmp_path, *, default_anchor=False, depth_sub=False):
    """A catalog + session with an EMPTY private workspace (nothing initialized),
    mimicking the post-session-creation state before init_workspace runs."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    locks._fake_hold_for_tests(cat_dir)
    try:
        cat = Catalog(cat_dir)
        cat.ensure_created()
        root = cat.create_schedule(DUMMY_SOURCE, parent_idea=None)
        idea = cat.create_idea(root, "seed", "seed proposal\n")
        dup = cat.create_schedule(DUMMY_SOURCE, parent_idea=idea)
        idea.set_canonical(dup.full_id)
        anchor_id = dup.full_id if default_anchor else None
        sess = cat.create_session(idea, None, 0, prompt="do the thing\n",
                                  default_anchor_schedule_id=anchor_id)
        sid = sess.full_id
        if depth_sub:
            sub_idea = cat.create_idea(dup, "subseed", "sub\n")
            subdup = cat.create_schedule(DUMMY_SOURCE, parent_idea=sub_idea)
            sub_idea.set_canonical(subdup.full_id)
            sub = cat.create_session(sub_idea, sess, 1, prompt="sub\n")
            sid = sub.full_id
        cat.flush()
        safety.commit()
        return Sess(cat_dir, sid)
    finally:
        locks._reset_for_tests()
        safety._new_entries.clear()
        safety._pending_overwrites.clear()


def _out(run_tool, capsys, fn, args):
    capsys.readouterr()
    run_tool(fn, args)
    return capsys.readouterr().out


def test_init_workspace_creates_files(tmp_path, run_tool, capsys):
    S = _bare_session(tmp_path)
    run_tool(tools.cmd_init_workspace, S.ns(force=False))
    priv = S.private_dir
    assert open(os.path.join(priv, "generator.cpp")).read() == DUMMY_SOURCE
    assert json.load(open(os.path.join(priv, "generator_parameters.json"))) == [{}]
    # current idea state points at the 0th seed idea.
    assert "dendritic_hl_idea(" in open(
        os.path.join(priv, "current_idea_state.txt")).read()
    # private idea list: the seed idea at pool tag "default".
    ideas = json.load(open(os.path.join(priv, "private_ideas.json")))
    assert list(ideas.values()) == ["default"]
    assert json.load(open(os.path.join(priv, "private_benchmark_sets.json"))) == {}
    # And status now reports a consistent workspace.
    out = _out(run_tool, capsys, tools.cmd_status, S.ns())
    assert "workspace consistent" in out


def test_init_workspace_refuses_without_force(tmp_path, run_tool, capsys):
    S = _bare_session(tmp_path)
    run_tool(tools.cmd_init_workspace, S.ns(force=False))
    capsys.readouterr()
    with pytest.raises(DhHlError, match="already initialized"):
        run_tool(tools.cmd_init_workspace, S.ns(force=False))
    # The depth-0 AGENTS guidance is printed.
    assert "AGENTS:" in capsys.readouterr().out


def test_init_workspace_force_reinitializes(tmp_path, run_tool):
    S = _bare_session(tmp_path)
    run_tool(tools.cmd_init_workspace, S.ns(force=False))
    S.write_workspace("scribbled\n")
    run_tool(tools.cmd_init_workspace, S.ns(force=True))
    assert open(S.workspace_path).read() == DUMMY_SOURCE  # restored


def test_init_workspace_sub_session_warning(tmp_path, run_tool, capsys):
    S = _bare_session(tmp_path, depth_sub=True)
    run_tool(tools.cmd_init_workspace, S.ns(force=False))
    capsys.readouterr()
    with pytest.raises(DhHlError):
        run_tool(tools.cmd_init_workspace, S.ns(force=False))
    assert "STOP IMMEDIATELY" in capsys.readouterr().out  # the sub-agent variant


# ---- current anchor -------------------------------------------------------

def test_default_anchor_seeds_current_anchor(tmp_path, run_tool, capsys):
    S = _bare_session(tmp_path, default_anchor=True)
    run_tool(tools.cmd_init_workspace, S.ns(force=False))
    out = _out(run_tool, capsys, tools.cmd_get_current_anchor, S.ns())
    assert out.strip() != "none"  # the default anchor became the current anchor


def test_no_default_anchor_means_none(tmp_path, run_tool, capsys):
    S = _bare_session(tmp_path, default_anchor=False)
    run_tool(tools.cmd_init_workspace, S.ns(force=False))
    out = _out(run_tool, capsys, tools.cmd_get_current_anchor, S.ns())
    assert out.strip() == "none"


def test_set_and_clear_current_anchor(tmp_path, run_tool, capsys):
    S = _bare_session(tmp_path)
    run_tool(tools.cmd_init_workspace, S.ns(force=False))
    # Set the anchor to the consistent workspace's schedule (default [schedule ID]).
    run_tool(tools.cmd_set_current_anchor, S.ns(schedule=None))
    assert _out(run_tool, capsys, tools.cmd_get_current_anchor,
                S.ns()).strip() != "none"
    run_tool(tools.cmd_set_current_anchor, S.ns(schedule="none"))
    assert _out(run_tool, capsys, tools.cmd_get_current_anchor,
                S.ns()).strip() == "none"
