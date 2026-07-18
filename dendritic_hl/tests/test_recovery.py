"""The rare recovery tools: force_parent_idea and fix_canonical."""

import json
import os

import pytest

from dendritic_hl_lib import ids, safety, tools
from dendritic_hl_lib.catalog import Catalog
from dendritic_hl_lib.errors import DhHlError
from conftest import ns, open_catalog


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


# ---------------------------------------------------------------------------
# force_parent_idea: attach a regretted root to an existing idea as canonical
# ---------------------------------------------------------------------------

def test_force_parent_idea(session, run_tool, tmp_path, capsys):
    # Make a fresh root R1 from edited workspace content.
    session.write_workspace("source A\n")
    run_tool(tools.cmd_new_root, session.ns())                  # root R1
    prop = _write(tmp_path, "p.txt", "An idea under R1.\n")
    run_tool(tools.cmd_new_idea, session.ns(proposal_name="idea1", proposal=prop))
    capsys.readouterr()
    run_tool(tools.cmd_json_idea_info, session.ns(idea=".idea1"))
    idea1_full = json.loads(capsys.readouterr().out)["id"]

    # A different workspace -> a second, regretted root R2 (newer timestamp).
    session.write_workspace("source A\n// variant B\n")
    run_tool(tools.cmd_new_root, session.ns())                  # root R2 (current)
    capsys.readouterr()
    run_tool(tools.cmd_json_schedule_info, session.ns())        # default = R2
    r2_full = json.loads(capsys.readouterr().out)["id"]

    # Attach R2 (default schedule = the unambiguous current node) under idea1.
    run_tool(tools.cmd_force_parent_idea, session.ns(idea=".idea1", schedule=None))
    capsys.readouterr()

    run_tool(tools.cmd_json_idea_info, session.ns(idea=idea1_full))
    idea_obj = json.loads(capsys.readouterr().out)
    assert idea_obj["canonical_schedule"] == r2_full
    assert r2_full in idea_obj["children"]

    run_tool(tools.cmd_json_schedule_info, session.ns(schedule=r2_full))
    assert json.loads(capsys.readouterr().out)["parent"] == idea1_full


def test_force_parent_idea_rejects_non_root(tmp_path, run_tool):
    """force_parent_idea requires a root; a child schedule must be refused."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    I = cat.create_idea(R, "vec", "v\n")
    C = cat.create_schedule("child", parent_idea=I)
    I2 = cat.create_idea(R, "vec2", "v2\n")
    cat.flush()
    safety.commit()

    with pytest.raises(DhHlError, match="requires a root schedule"):
        run_tool(tools.cmd_force_parent_idea,
                 ns(catalog=cat_dir, idea=I2.full_id, schedule=C.full_id))


# ---------------------------------------------------------------------------
# fix_canonical: resolve a canonical.txt merge conflict
# ---------------------------------------------------------------------------

def _build_conflict(tmp_path):
    """root R -> idea I with two child schedules C1(older), C2(newer), and a
    merge-conflicted canonical.txt listing both.  Returns (cat_dir, ids...)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root source", parent_idea=None)
    I = cat.create_idea(R, "vec", "Vectorize.\n")
    C1 = cat.create_schedule("child one", parent_idea=I)   # older
    C2 = cat.create_schedule("child two", parent_idea=I)   # newer
    cat.flush()
    safety.commit()
    with open(os.path.join(I.dir, "canonical.txt"), "w") as f:
        f.write(C1.full_id + "\n" + C2.full_id + "\n")
    assert C1.full_id < C2.full_id  # older sorts first
    return cat_dir, I.full_id, C1.full_id, C2.full_id


def test_fix_canonical(tmp_path, run_tool, capsys):
    cat_dir, idea_full, c1, c2 = _build_conflict(tmp_path)

    run_tool(tools.cmd_fix_canonical, ns(catalog=cat_dir, idea=idea_full))
    capsys.readouterr()

    run_tool(tools.cmd_json_idea_info, ns(catalog=cat_dir, idea=idea_full))
    assert json.loads(capsys.readouterr().out)["canonical_schedule"] == c1

    run_tool(tools.cmd_json_schedule_info, ns(catalog=cat_dir, schedule=c2))
    c2_parent = json.loads(capsys.readouterr().out)["parent"]
    assert c2_parent != idea_full
    assert ids.idea_proposal_name(c2_parent).startswith("fix_canonical")

    run_tool(tools.cmd_json_idea_info, ns(catalog=cat_dir, idea=c2_parent))
    fix_idea = json.loads(capsys.readouterr().out)
    assert fix_idea["parent"] == c1
    assert fix_idea["canonical_schedule"] == c2


def test_fix_canonical_requires_two_ids(tmp_path, run_tool):
    """If canonical.txt doesn't hold exactly two competing IDs, refuse."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    I = cat.create_idea(R, "vec", "v\n")
    C = cat.create_schedule("child", parent_idea=I)
    I.set_canonical(C.full_id)  # a normal single-canonical (no conflict)
    cat.flush()
    safety.commit()
    with pytest.raises(DhHlError, match="exactly 2"):
        run_tool(tools.cmd_fix_canonical, ns(catalog=cat_dir, idea=I.full_id))
