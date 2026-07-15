"""The list-schedules tree-traversal tools: list_sibling_schedules,
list_child_schedules, list_equal_schedules.

Halide-free: the tree is built directly through the catalog API (as in
test_recovery), then the read-only tools are driven via cmd_*."""

import pytest

from dendritic_hl_lib import safety, tools
from dendritic_hl_lib.catalog import Catalog
from dendritic_hl_lib.context import catalog_dir_for
from dendritic_hl_lib.errors import DhHlError
from conftest import ns


def _build(workspace):
    """root R --idea I--> {C1 (canonical), C2}; a second idea E under R with no
    children; and a lone root R2 whose source equals C2's (an equal-hash pair).
    Returns short/full IDs of interest."""
    cat = Catalog(str(workspace) + ".dh_hl", str(workspace))
    cat.ensure_created()
    R = cat.create_schedule("root src", parent_idea=None)
    I = cat.create_idea(R, "vec", "Vectorize.\n")
    C1 = cat.create_schedule("child one", parent_idea=I)
    C2 = cat.create_schedule("child two", parent_idea=I)
    E = cat.create_idea(R, "empty", "Empty.\n")
    for c in (C1, C2):
        c.set_result("success")
    I.set_canonical(C1.full_id)
    # A separate root whose source duplicates C2 -> same hash as C2.
    R2 = cat.create_schedule("child two", parent_idea=None)
    cat.flush()
    safety.commit()
    return {"R": R.full_id, "I": I.full_id, "E": E.full_id,
            "C1": C1.full_id, "C2": C2.full_id, "R2": R2.full_id}


def _resolved_headers(workspace, out):
    """Full IDs of the schedule blocks printed (the `Schedule:` headers show
    *short* IDs, so resolve them back through a catalog)."""
    shorts = [ln.split("Schedule: ", 1)[1]
              for ln in out.splitlines() if ln.startswith("Schedule: ")]
    cat = Catalog(catalog_dir_for(str(workspace)), str(workspace))
    return [cat.resolve_schedule(s).full_id for s in shorts]


# ---- list_child_schedules -------------------------------------------------

def test_list_child_schedules_lists_all_children(workspace, capsys):
    t = _build(workspace)
    tools.cmd_list_child_schedules(ns(workspace=str(workspace), idea=t["I"]))
    out = capsys.readouterr().out
    assert set(_resolved_headers(workspace, out)) == {t["C1"], t["C2"]}


def test_list_child_schedules_empty_idea(workspace, capsys):
    t = _build(workspace)
    tools.cmd_list_child_schedules(ns(workspace=str(workspace), idea=t["E"]))
    assert "no matching schedule nodes" in capsys.readouterr().out


# ---- list_sibling_schedules -----------------------------------------------

def test_list_sibling_schedules_includes_all_under_same_idea(workspace, capsys):
    t = _build(workspace)
    # Ask from C1's perspective; siblings are all children of idea I (incl. C1).
    tools.cmd_list_sibling_schedules(ns(workspace=str(workspace),
                                        schedule=t["C1"]))
    out = capsys.readouterr().out
    assert set(_resolved_headers(workspace, out)) == {t["C1"], t["C2"]}


def test_list_sibling_schedules_rejects_root(workspace):
    t = _build(workspace)
    with pytest.raises(DhHlError, match="non-root"):
        tools.cmd_list_sibling_schedules(ns(workspace=str(workspace),
                                            schedule=t["R"]))


# ---- list_equal_schedules -------------------------------------------------

def test_list_equal_schedules_matches_by_hash(workspace, capsys):
    t = _build(workspace)
    # C2 and R2 share source "child two" -> same hash; C1 differs.
    tools.cmd_list_equal_schedules(ns(workspace=str(workspace), schedule=t["C2"]))
    out = capsys.readouterr().out
    assert set(_resolved_headers(workspace, out)) == {t["C2"], t["R2"]}


def test_list_equal_schedules_singleton(workspace, capsys):
    t = _build(workspace)
    tools.cmd_list_equal_schedules(ns(workspace=str(workspace), schedule=t["C1"]))
    out = capsys.readouterr().out
    assert _resolved_headers(workspace, out) == [t["C1"]]  # unique hash
