"""Private benchmark set list: the add/remove/list CLI tools, and a regression
guard that the lazy-load-once + flush-once object model persists *every* item of
a looped mutation (the pre-object code re-read the file each call and only the
last write survived -- see context.PrivateBenchmarkSetList)."""

import pytest

from dendritic_hl_lib import locks, safety, tools
from dendritic_hl_lib.context import SessionWorkspace
from dendritic_hl_lib.errors import DhHlError
from conftest import add_synthetic_benchmark_set, open_catalog


def _out(run_tool, capsys, fn, args):
    capsys.readouterr()
    run_tool(fn, args)
    return capsys.readouterr().out


def _two_sets(session):
    """Create two real benchmark sets over a child schedule; return their IDs."""
    cat = open_catalog(session.catalog_dir)
    try:
        idea = cat.get_idea(cat.get_session(session.session_id).seed_idea_id)
        A = cat.create_schedule("A source\n", parent_idea=idea)
        s1 = add_synthetic_benchmark_set(cat, {A.full_id: [[100, 101, 99]]})
        s2 = add_synthetic_benchmark_set(cat, {A.full_id: [[90, 91, 89]]})
        cat.flush(); safety.commit()
        return s1, s2
    finally:
        locks._reset_for_tests()


# ---- regression: looped mutation persists every item ----------------------

def test_multi_add_persists_all(session):
    """Two add() calls before a single flush -> BOTH survive (the object
    accumulates in memory; the old read-file-per-call code lost the first)."""
    s1, s2 = _two_sets(session)
    cat = open_catalog(session.catalog_dir)
    try:
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        ws.add_private_benchmark_set(s1, cat)
        ws.add_private_benchmark_set(s2, cat)
        cat.flush(); safety.commit()
    finally:
        locks._reset_for_tests()
    # Re-open a fresh workspace so it lazy-loads from disk.
    cat = open_catalog(session.catalog_dir)
    try:
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        assert set(ws.read_private_benchmark_sets()) == {s1, s2}
    finally:
        locks._reset_for_tests()


def test_multi_pool_tag_persists_all(session):
    """The same accumulation guarantee for the private idea list (join_session /
    new_sub_session set several pool tags in one run)."""
    cat = open_catalog(session.catalog_dir)
    try:
        idea = cat.get_idea(cat.get_session(session.session_id).seed_idea_id)
        i2 = cat.create_idea(cat.get_schedule(idea.parent_id), "two", "p\n")
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        ws.set_pool_tag(idea.full_id, "one")
        ws.set_pool_tag(i2.full_id, "two")
        cat.flush(); safety.commit()
        ids = {idea.full_id: "one", i2.full_id: "two"}
    finally:
        locks._reset_for_tests()
    cat = open_catalog(session.catalog_dir)
    try:
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        got = ws.read_private_ideas()
        assert got[list(ids)[0]] == "one" and got[list(ids)[1]] == "two"
    finally:
        locks._reset_for_tests()


# ---- CLI tools ------------------------------------------------------------

def test_add_list_remove_roundtrip(session, run_tool, capsys):
    s1, s2 = _two_sets(session)
    run_tool(tools.cmd_add_private_benchmark_set,
             session.ns(benchmark_sets=[s1, s2]))
    out = _out(run_tool, capsys, tools.cmd_list_private_benchmark_sets,
               session.ns())
    assert out.splitlines() == sorted([s1, s2])   # sorted, one per line

    run_tool(tools.cmd_remove_private_benchmark_set,
             session.ns(benchmark_sets=[s1]))
    out = _out(run_tool, capsys, tools.cmd_list_private_benchmark_sets,
               session.ns())
    assert out.splitlines() == [s2]


def test_add_rejects_unknown_set(session, run_tool):
    with pytest.raises(DhHlError, match="no such benchmark set|not.*benchmark set"):
        run_tool(tools.cmd_add_private_benchmark_set,
                 session.ns(benchmark_sets=["Nohost_2026-01-01T000000_000000Z"]))


def test_remove_absent_is_silent_noop(session, run_tool, capsys):
    s1, _ = _two_sets(session)
    run_tool(tools.cmd_add_private_benchmark_set, session.ns(benchmark_sets=[s1]))
    # Removing something not in the list prints nothing and leaves s1.
    out = _out(run_tool, capsys, tools.cmd_remove_private_benchmark_set,
               session.ns(benchmark_sets=["Nohost_2026-01-01T000000_000000Z"]))
    assert out == ""
    out = _out(run_tool, capsys, tools.cmd_list_private_benchmark_sets,
               session.ns())
    assert out.splitlines() == [s1]


def test_empty_list_is_noop(session, run_tool, capsys):
    out = _out(run_tool, capsys, tools.cmd_add_private_benchmark_set,
               session.ns(benchmark_sets=[]))
    assert out == ""


def test_list_takes_session_lock(session, run_tool):
    run_tool(tools.cmd_list_private_benchmark_sets, session.ns())
    assert ("session", "exclusive") in locks._trace_sink
