"""Actionable advice + status details for the 'edited a canonical without
branching an idea first' failure mode."""

import os

import pytest

from dendritic_hl_lib import ids, safety, tools
from dendritic_hl_lib.catalog import Catalog
from dendritic_hl_lib.context import SessionWorkspace
from dendritic_hl_lib.enums import IdeaStateKind, Result
from dendritic_hl_lib.errors import DhHlError
from conftest import Sess, open_catalog


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _build(tmp_path):
    """Catalog with root R -> idea I{canonical=C1, child C2}, idea I2{child C3,
    no canon}, plus a top-level session seeded with I.  Returns (Sess, ids)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    I = cat.create_idea(R, "vec", "Vectorize.\n")
    C1 = cat.create_schedule("child one", parent_idea=I)
    C2 = cat.create_schedule("child two", parent_idea=I)
    I2 = cat.create_idea(R, "tile", "Tile.\n")
    C3 = cat.create_schedule("child three", parent_idea=I2)
    for c in (C1, C2, C3):
        c.set_result(Result.SUCCESS)
    I.set_canonical(C1.full_id)
    sess = cat.create_session(I, None, 0)
    ws = SessionWorkspace(cat.catalog_dir, sess.full_id, catalog=cat)
    ws.initialize("root", (IdeaStateKind.IDEA, I.full_id))
    cat.flush()
    safety.commit()
    t = {"R": R.full_id, "I": I.full_id, "I2": I2.full_id,
         "C1": C1.full_id, "C2": C2.full_id, "C3": C3.full_id,
         "C1_src": "child one", "C2_src": "child two"}
    return Sess(cat_dir, sess.full_id), t


# ---- new_idea on a minor schedule -----------------------------------------

def test_new_idea_on_minor_with_canonical_points_at_canonical(
        tmp_path, run_tool):
    S, t = _build(tmp_path)
    prop = _write(tmp_path, "p.txt", "text")
    with pytest.raises(DhHlError) as e:
        run_tool(tools.cmd_new_idea,
                 S.ns(proposal_name="x", proposal=prop, schedule=t["C2"]))
    msg = str(e.value)
    assert "minor schedule" in msg
    # steer to parent idea's canonical (C1), not C2, with correct arg order.
    assert "dh_hl new_idea <name> <proposal file>" in msg
    assert "canonical" in msg


def test_new_idea_on_minor_without_canonical_suggests_canon(tmp_path, run_tool):
    S, t = _build(tmp_path)
    prop = _write(tmp_path, "p.txt", "text")
    with pytest.raises(DhHlError) as e:
        run_tool(tools.cmd_new_idea,
                 S.ns(proposal_name="x", proposal=prop, schedule=t["C3"]))
    msg = str(e.value)
    assert "minor schedule" in msg
    assert "dh_hl canon" in msg


# ---- canon on an idea that already has a canonical ------------------------

def test_canon_blocked_names_the_canonical(tmp_path, run_tool, capsys):
    S, t = _build(tmp_path)
    # Make the workspace look like C2 (a minor child of I) and select idea I.
    S.write_workspace(t["C2_src"])
    run_tool(tools.cmd_set_idea, S.ns(idea=t["I"]))
    capsys.readouterr()
    with pytest.raises(DhHlError) as e:
        run_tool(tools.cmd_canon, S.ns())
    msg = str(e.value)
    assert "already has a canonical schedule" in msg
    # Golden line: new_idea takes <name> <proposal file> BEFORE the schedule ID
    # (the blocking canonical), matching its argparse signature.
    assert "dh_hl new_idea <name> <proposal file>" in msg
    assert "dh_hl set_idea" in msg


# ---- status: canonical-schedule status + dangling idea --------------------

def test_status_reports_canonical_schedule(tmp_path, run_tool, capsys):
    S, t = _build(tmp_path)
    run_tool(tools.cmd_set_idea, S.ns(idea=t["I"]))
    capsys.readouterr()
    run_tool(tools.cmd_status, S.ns())
    out = capsys.readouterr().out
    assert "Current idea's canonical schedule:" in out
    assert "none" not in out.split("canonical schedule:")[1].splitlines()[0]


def test_status_reports_no_canonical(tmp_path, run_tool, capsys):
    S, t = _build(tmp_path)
    run_tool(tools.cmd_set_idea, S.ns(idea=t["I2"]))  # no canon
    capsys.readouterr()
    run_tool(tools.cmd_status, S.ns())
    out = capsys.readouterr().out
    assert "Current idea's canonical schedule: none" in out


def test_status_warns_on_dangling_idea(tmp_path, run_tool, capsys):
    S, _ = _build(tmp_path)
    # Point the current idea state at a syntactically valid but absent idea.
    ghost = ids.make_idea_id(
        "ghost", ids.make_schedule_id(ids.now_timestamp(), "f" * 64))
    with open(os.path.join(S.private_dir, "current_idea_state.txt"),
              "w", encoding="utf-8") as f:
        f.write("dendritic_hl_idea({})\n".format(ghost))
    run_tool(tools.cmd_status, S.ns())
    out = capsys.readouterr().out
    assert "nonexistent idea node" in out
    assert ghost in out
