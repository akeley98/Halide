"""The rare recovery tools: force_parent_idea and fix_canonical."""

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


# ---------------------------------------------------------------------------
# force_parent_idea: attach a regretted root to an existing idea as canonical
# ---------------------------------------------------------------------------

def test_force_parent_idea(workspace, tmp_path, capsys):
    ws = str(workspace)
    content_a = workspace.read_text()

    tools.cmd_new_root(ns(workspace=ws))                     # root R1
    prop = _write(tmp_path, "p.txt", "An idea under R1.\n")
    tools.cmd_new_idea(ns(workspace=ws, proposal_name="idea1", proposal=prop))
    capsys.readouterr()
    tools.cmd_json_idea_info(ns(workspace=ws, idea=".idea1"))
    idea1_full = json.loads(capsys.readouterr().out)["id"]

    # A different workspace -> a second, regretted root R2 (newer timestamp).
    workspace.write_text(content_a + "\n// variant B\n")
    tools.cmd_new_root(ns(workspace=ws))                     # root R2 (current)
    capsys.readouterr()
    tools.cmd_json_schedule_info(ns(workspace=ws))           # default = R2
    r2_full = json.loads(capsys.readouterr().out)["id"]

    # Attach R2 (default schedule = the unambiguous current node) under idea1.
    tools.cmd_force_parent_idea(ns(workspace=ws, idea=".idea1", schedule=None))
    capsys.readouterr()

    # idea1 now has R2 as its canonical schedule and as a child.
    tools.cmd_json_idea_info(ns(workspace=ws, idea=idea1_full))
    idea_obj = json.loads(capsys.readouterr().out)
    assert idea_obj["canonical_schedule"] == r2_full
    assert r2_full in idea_obj["children"]

    # R2 is no longer a root: its parent is idea1.
    tools.cmd_json_schedule_info(ns(workspace=ws, schedule=r2_full))
    assert json.loads(capsys.readouterr().out)["parent"] == idea1_full


def test_force_parent_idea_rejects_non_root(workspace, tmp_path, capsys):
    """force_parent_idea requires a root; a child schedule must be refused."""
    # Build root -> idea -> child directly (no Halide needed).
    cat_dir = str(workspace) + ".dh_hl"
    cat = Catalog(cat_dir, str(workspace))
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    I = cat.create_idea(R, "vec", "v\n")
    C = cat.create_schedule("child", parent_idea=I)
    # A second idea to try (illegally) parenting the child under.
    I2 = cat.create_idea(R, "vec2", "v2\n")
    cat.flush()
    safety.commit()

    with pytest.raises(DhHlError, match="requires a root schedule"):
        tools.cmd_force_parent_idea(
            ns(workspace=str(workspace), idea=I2.full_id, schedule=C.full_id))


# ---------------------------------------------------------------------------
# fix_canonical: resolve a canonical.txt merge conflict
# ---------------------------------------------------------------------------

def _build_conflict(workspace):
    """root R -> idea I with two child schedules C1(older), C2(newer), and a
    merge-conflicted canonical.txt listing both.  Returns their full IDs."""
    cat_dir = str(workspace) + ".dh_hl"
    cat = Catalog(cat_dir, str(workspace))
    cat.ensure_created()
    R = cat.create_schedule("root source", parent_idea=None)
    I = cat.create_idea(R, "vec", "Vectorize.\n")
    C1 = cat.create_schedule("child one", parent_idea=I)   # older
    C2 = cat.create_schedule("child two", parent_idea=I)   # newer
    cat.flush()
    safety.commit()
    # Simulate the merge conflict: canonical.txt with both IDs.
    with open(os.path.join(I.dir, "canonical.txt"), "w") as f:
        f.write(C1.full_id + "\n" + C2.full_id + "\n")
    assert C1.full_id < C2.full_id  # older sorts first
    return I.full_id, C1.full_id, C2.full_id


def test_fix_canonical(workspace, capsys):
    idea_full, c1, c2 = _build_conflict(workspace)
    ws = str(workspace)

    tools.cmd_fix_canonical(ns(workspace=ws, idea=idea_full))
    capsys.readouterr()

    # The older child (C1) is now the single canonical of the original idea.
    tools.cmd_json_idea_info(ns(workspace=ws, idea=idea_full))
    assert json.loads(capsys.readouterr().out)["canonical_schedule"] == c1

    # The newer child (C2) was re-parented under an auto-generated fix idea...
    tools.cmd_json_schedule_info(ns(workspace=ws, schedule=c2))
    c2_parent = json.loads(capsys.readouterr().out)["parent"]
    assert c2_parent != idea_full
    assert ids.idea_proposal_name(c2_parent).startswith("fix_canonical")

    # ...whose parent schedule is C1 and whose canonical schedule is C2.
    tools.cmd_json_idea_info(ns(workspace=ws, idea=c2_parent))
    fix_idea = json.loads(capsys.readouterr().out)
    assert fix_idea["parent"] == c1
    assert fix_idea["canonical_schedule"] == c2


def test_fix_canonical_requires_two_ids(workspace, capsys):
    """If canonical.txt doesn't hold exactly two competing IDs, refuse."""
    cat_dir = str(workspace) + ".dh_hl"
    cat = Catalog(cat_dir, str(workspace))
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    I = cat.create_idea(R, "vec", "v\n")
    C = cat.create_schedule("child", parent_idea=I)
    I.set_canonical(C.full_id)  # a normal single-canonical (no conflict)
    cat.flush()
    safety.commit()
    with pytest.raises(DhHlError, match="exactly 2"):
        tools.cmd_fix_canonical(ns(workspace=str(workspace), idea=I.full_id))
