"""Short-ID resolution and formatting (round-trip, preferred forms, errors)."""

import pytest

from dendritic_hl_lib import safety
from dendritic_hl_lib.catalog import Catalog
from dendritic_hl_lib.errors import DhHlError


def build_tree(tmp_path):
    """root R --idea I(vec)--> child C(canonical).  Flush, then reopen."""
    cat_dir = str(tmp_path / "gen.cpp.dh_hl")
    ws = str(tmp_path / "gen.cpp")
    cat = Catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root source", parent_idea=None)
    I = cat.create_idea(R, "vec", "Vectorize.\n")
    C = cat.create_schedule("child source", parent_idea=I)
    I.set_canonical(C.full_id)
    cat.flush()
    safety.commit()
    # Reopen fresh so resolution reads purely from disk-derived dicts.
    return Catalog(cat_dir), R.full_id, I.full_id, C.full_id


def test_roundtrip_all_nodes(reset_safety, tmp_path):
    cat, rid, iid, cid = build_tree(tmp_path)
    for full in (rid, cid):
        node = cat.schedules[full]
        short = cat.format_schedule_id(node)
        assert cat.resolve_schedule(short).full_id == full
    idea = cat.ideas[iid]
    short = cat.format_idea_id(idea)
    assert cat.resolve_idea(short).full_id == iid


def test_root_format_and_resolve(reset_safety, tmp_path):
    cat, rid, iid, cid = build_tree(tmp_path)
    short = cat.format_schedule_id(cat.schedules[rid])
    assert short.startswith("root.")
    assert cat.resolve_schedule(short).full_id == rid


def test_canonical_preferred_form(reset_safety, tmp_path):
    cat, rid, iid, cid = build_tree(tmp_path)
    short = cat.format_schedule_id(cat.schedules[cid])
    assert short.endswith(".canon")
    assert cat.resolve_schedule(short).full_id == cid
    # The explicit two-dot canon form resolves too.
    assert cat.resolve_schedule(".vec.canon").full_id == cid


def test_idea_short_form(reset_safety, tmp_path):
    cat, rid, iid, cid = build_tree(tmp_path)
    assert cat.resolve_idea(".vec").full_id == iid
    # hash-prefixed idea short id
    parent_hash = iid[-64:] if False else None  # (parent hash lives in id)
    from dendritic_hl_lib import ids
    hp = ids.schedule_hash(ids.idea_parent_id(iid))[:6]
    assert cat.resolve_idea("{}.vec".format(hp)).full_id == iid


def test_full_ids_accepted(reset_safety, tmp_path):
    cat, rid, iid, cid = build_tree(tmp_path)
    assert cat.resolve_schedule(rid).full_id == rid
    assert cat.resolve_idea(iid).full_id == iid


def test_unknown_short_id_raises(reset_safety, tmp_path):
    cat, rid, iid, cid = build_tree(tmp_path)
    with pytest.raises(DhHlError):
        cat.resolve_schedule("deadbeef")          # no such hash prefix
    with pytest.raises(DhHlError):
        cat.resolve_idea(".nonexistent_name")


def test_bare_hash_prefix_matches_child(reset_safety, tmp_path):
    cat, rid, iid, cid = build_tree(tmp_path)
    from dendritic_hl_lib import ids
    child_hash = ids.schedule_hash(cid)
    # A hash prefix unique to the child resolves to it.
    assert cat.resolve_schedule(child_hash[:12]).full_id == cid
