"""The list-schedules tree-traversal tools: list_sibling_schedules,
list_child_schedules, list_equal_schedules.

Halide-free: the tree is built directly through the catalog API (as in
test_recovery), then the read-only -C tools are driven via cmd_* / run_tool."""

import pytest

from dendritic_hl_lib import safety, tools
from dendritic_hl_lib.catalog import Catalog
from dendritic_hl_lib.errors import DhHlError
from conftest import ns


def _build(tmp_path):
    """root R --idea I--> {C1 (canonical), C2}; a second idea E under R with no
    children; and a lone root R2 whose source equals C2's (an equal-hash pair).
    Returns cat_dir plus short/full IDs of interest."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = Catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root src", parent_idea=None)
    I = cat.create_idea(R, "vec", "Vectorize.\n")
    C1 = cat.create_schedule("child one", parent_idea=I)
    C2 = cat.create_schedule("child two", parent_idea=I)
    E = cat.create_idea(R, "empty", "Empty.\n")
    for c in (C1, C2):
        c.set_result("success")
    I.set_canonical(C1.full_id)
    R2 = cat.create_schedule("child two", parent_idea=None)  # same hash as C2
    cat.flush()
    safety.commit()
    return {"cat": cat_dir, "R": R.full_id, "I": I.full_id, "E": E.full_id,
            "C1": C1.full_id, "C2": C2.full_id, "R2": R2.full_id}


def _resolved_headers(cat_dir, out):
    """Full IDs of the schedule blocks printed (the `Schedule:` headers show
    *short* IDs, so resolve them back through a catalog)."""
    shorts = [ln.split("Schedule: ", 1)[1]
              for ln in out.splitlines() if ln.startswith("Schedule: ")]
    cat = Catalog(cat_dir)
    return [cat.resolve_schedule(s).full_id for s in shorts]


# ---- list_child_schedules -------------------------------------------------

def test_list_child_schedules_lists_all_children(tmp_path, run_tool, capsys):
    t = _build(tmp_path)
    run_tool(tools.cmd_list_child_schedules, ns(catalog=t["cat"], idea=t["I"]))
    out = capsys.readouterr().out
    assert set(_resolved_headers(t["cat"], out)) == {t["C1"], t["C2"]}


def test_list_child_schedules_empty_idea(tmp_path, run_tool, capsys):
    t = _build(tmp_path)
    run_tool(tools.cmd_list_child_schedules, ns(catalog=t["cat"], idea=t["E"]))
    assert "no matching schedule nodes" in capsys.readouterr().out


# ---- list_sibling_schedules -----------------------------------------------

def test_list_sibling_schedules_includes_all_under_same_idea(
        tmp_path, run_tool, capsys):
    t = _build(tmp_path)
    run_tool(tools.cmd_list_sibling_schedules,
             ns(catalog=t["cat"], schedule=t["C1"]))
    out = capsys.readouterr().out
    assert set(_resolved_headers(t["cat"], out)) == {t["C1"], t["C2"]}


def test_list_sibling_schedules_rejects_root(tmp_path, run_tool):
    t = _build(tmp_path)
    with pytest.raises(DhHlError, match="non-root"):
        run_tool(tools.cmd_list_sibling_schedules,
                 ns(catalog=t["cat"], schedule=t["R"]))


# ---- list_equal_schedules -------------------------------------------------

def test_list_equal_schedules_matches_by_hash(tmp_path, run_tool, capsys):
    t = _build(tmp_path)
    run_tool(tools.cmd_list_equal_schedules, ns(catalog=t["cat"], schedule=t["C2"]))
    out = capsys.readouterr().out
    assert set(_resolved_headers(t["cat"], out)) == {t["C2"], t["R2"]}


def test_list_equal_schedules_singleton(tmp_path, run_tool, capsys):
    t = _build(tmp_path)
    run_tool(tools.cmd_list_equal_schedules, ns(catalog=t["cat"], schedule=t["C1"]))
    out = capsys.readouterr().out
    assert _resolved_headers(t["cat"], out) == [t["C1"]]  # unique hash
