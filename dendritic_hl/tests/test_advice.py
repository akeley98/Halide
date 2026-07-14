"""Actionable advice + status details for the 'edited a canonical without
branching an idea first' failure mode."""

import json
import os

import pytest

from dendritic_hl_lib import ids, safety, tools
from dendritic_hl_lib.catalog import Catalog
from dendritic_hl_lib.errors import DhHlError
from conftest import ns


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _build(workspace):
    """root R -> idea I{canonical=C1, child C2}, and idea I2{child C3, no canon}.
    Returns a dict of full IDs.  All child schedules built as 'success'."""
    cat = Catalog(str(workspace) + ".dh_hl", str(workspace))
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    I = cat.create_idea(R, "vec", "Vectorize.\n")
    C1 = cat.create_schedule("child one", parent_idea=I)
    C2 = cat.create_schedule("child two", parent_idea=I)
    I2 = cat.create_idea(R, "tile", "Tile.\n")
    C3 = cat.create_schedule("child three", parent_idea=I2)
    for c in (C1, C2, C3):
        c.set_result("success")
    I.set_canonical(C1.full_id)
    cat.flush()
    safety.commit()
    return {"R": R.full_id, "I": I.full_id, "I2": I2.full_id,
            "C1": C1.full_id, "C2": C2.full_id, "C3": C3.full_id,
            "C1_src": "child one", "C2_src": "child two"}


# ---- new_idea on a minor schedule -----------------------------------------

def test_new_idea_on_minor_with_canonical_points_at_canonical(
        workspace, tmp_path, capsys):
    t = _build(workspace)
    prop = _write(tmp_path, "p.txt", "text")
    with pytest.raises(DhHlError) as e:
        tools.cmd_new_idea(ns(workspace=str(workspace), proposal_name="x",
                              proposal=prop, schedule=t["C2"]))
    msg = str(e.value)
    assert "minor schedule" in msg
    assert "dh_hl new_idea" in msg
    # It should steer the user to the parent idea's canonical (C1), not C2.
    assert "canonical" in msg


def test_new_idea_on_minor_without_canonical_suggests_canon(
        workspace, tmp_path, capsys):
    t = _build(workspace)
    prop = _write(tmp_path, "p.txt", "text")
    # C3's parent idea (I2) has no canonical schedule.
    with pytest.raises(DhHlError) as e:
        tools.cmd_new_idea(ns(workspace=str(workspace), proposal_name="x",
                              proposal=prop, schedule=t["C3"]))
    msg = str(e.value)
    assert "minor schedule" in msg
    assert "dh_hl canon" in msg


# ---- canon on an idea that already has a canonical ------------------------

def test_canon_blocked_names_the_canonical(workspace, capsys):
    t = _build(workspace)
    # Make the workspace look like C2 (a minor child of I) and select idea I.
    workspace.write_text(t["C2_src"])
    tools.cmd_set_idea(ns(workspace=str(workspace), idea=t["I"]))
    capsys.readouterr()
    with pytest.raises(DhHlError) as e:
        tools.cmd_canon(ns(workspace=str(workspace)))
    msg = str(e.value)
    assert "already has a canonical schedule" in msg
    assert "dh_hl new_idea" in msg and "dh_hl set_idea" in msg


# ---- status: canonical-schedule status + dangling idea --------------------

def test_status_reports_canonical_schedule(workspace, capsys):
    t = _build(workspace)
    tools.cmd_set_idea(ns(workspace=str(workspace), idea=t["I"]))
    capsys.readouterr()
    tools.cmd_status(ns(workspace=str(workspace)))
    out = capsys.readouterr().out
    assert "Current idea's canonical schedule:" in out
    assert "none" not in out.split("canonical schedule:")[1].splitlines()[0]


def test_status_reports_no_canonical(workspace, capsys):
    t = _build(workspace)
    tools.cmd_set_idea(ns(workspace=str(workspace), idea=t["I2"]))  # no canon
    capsys.readouterr()
    tools.cmd_status(ns(workspace=str(workspace)))
    out = capsys.readouterr().out
    assert "Current idea's canonical schedule: none" in out


def test_status_warns_on_dangling_idea(workspace, capsys):
    _build(workspace)
    # Point the current idea state at a syntactically valid but absent idea.
    ghost = ids.make_idea_id(
        "ghost", ids.make_schedule_id(ids.now_timestamp(), "f" * 64))
    cis_path = os.path.join(str(workspace) + ".dh_hl", "current_idea_state.txt")
    with open(cis_path, "w") as f:
        f.write("dendritic_hl_idea({})\n".format(ghost))
    tools.cmd_status(ns(workspace=str(workspace)))
    out = capsys.readouterr().out
    assert "nonexistent idea node" in out
    assert ghost in out
