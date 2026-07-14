"""current_idea_state.txt parsing, incl. merge-conflict robustness."""

import os

import pytest

from dendritic_hl_lib import ids
from dendritic_hl_lib.catalog import Catalog


def make_catalog(tmp_path, state_text=None):
    cat_dir = tmp_path / "gen.cpp.dh_hl"
    cat_dir.mkdir()
    if state_text is not None:
        (cat_dir / "current_idea_state.txt").write_text(state_text)
    return Catalog(str(cat_dir), str(tmp_path / "gen.cpp"))


def a_schedule_id():
    return ids.make_schedule_id(ids.now_timestamp(), "c" * 64)


def a_idea_id():
    return ids.make_idea_id("some_idea", a_schedule_id())


def test_missing(tmp_path):
    cat = make_catalog(tmp_path)
    assert cat.current_idea_state.kind == "missing"


def test_no_idea(tmp_path):
    ts = ids.now_timestamp()
    cat = make_catalog(tmp_path, "dendritic_hl_root({})\n".format(ts))
    cis = cat.current_idea_state
    assert cis.kind == "no_idea"
    assert cis.timestamp == ts


def test_some_idea(tmp_path):
    iid = a_idea_id()
    cat = make_catalog(tmp_path, "dendritic_hl_idea({})\n".format(iid))
    cis = cat.current_idea_state
    assert cis.kind == "idea"
    assert cis.idea_id == iid


def test_cruft_lines_ignored(tmp_path):
    ts = ids.now_timestamp()
    text = "garbage\n\ndendritic_hl_root({})\n# a comment\n".format(ts)
    cat = make_catalog(tmp_path, text)
    cis = cat.current_idea_state
    assert cis.kind == "no_idea"
    assert cis.timestamp == ts


def test_merge_conflict_two_states_is_conflict(tmp_path):
    ts1 = ids.now_timestamp()
    iid = a_idea_id()
    text = (
        "<<<<<<< HEAD\n"
        "dendritic_hl_root({})\n"
        "=======\n"
        "dendritic_hl_idea({})\n"
        ">>>>>>> branch\n".format(ts1, iid))
    cat = make_catalog(tmp_path, text)
    cis = cat.current_idea_state
    assert cis.kind == "conflict"
    assert len(cis.parsed_lines) == 2


def test_conflict_raises_only_when_definite_state_needed(tmp_path):
    """A conflict must not raise on parse; only when a caller demands a
    definite current idea node."""
    ts1 = ids.now_timestamp()
    ts2 = ids.now_timestamp()
    text = "dendritic_hl_root({})\ndendritic_hl_root({})\n".format(ts1, ts2)
    cat = make_catalog(tmp_path, text)
    assert cat.current_idea_state.kind == "conflict"  # no raise
    with pytest.raises(Exception):
        cat.current_idea_node()  # now it must complain
