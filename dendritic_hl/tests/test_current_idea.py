"""current_idea_state.txt parsing, incl. merge-conflict robustness.

The current idea state now lives in a session's private workspace
(private/{id}/current_idea_state.txt); CurrentIdeaState is constructed with the
owning catalog (for dirty registration) and that private dir.  The parsing
logic is unchanged from when it was catalog-level.
"""

import pytest

from dendritic_hl_lib import ids
from dendritic_hl_lib.catalog import CurrentIdeaState
from dendritic_hl_lib.enums import IdeaStateKind


def make_cis(tmp_path, state_text=None):
    # These tests only parse (read) the state, so no catalog is needed.
    priv = tmp_path / "proj.dh_hl" / "private" / "sess"
    priv.mkdir(parents=True, exist_ok=True)
    if state_text is not None:
        (priv / "current_idea_state.txt").write_text(state_text)
    return CurrentIdeaState(str(priv))


def a_schedule_id():
    return ids.make_schedule_id(ids.now_timestamp(), "c" * 64)


def a_idea_id():
    return ids.make_idea_id("some_idea", a_schedule_id())


def test_missing(tmp_path):
    assert make_cis(tmp_path).kind == IdeaStateKind.MISSING


def test_no_idea(tmp_path):
    ts = ids.now_timestamp()
    cis = make_cis(tmp_path, "dendritic_hl_root({})\n".format(ts))
    assert cis.kind == IdeaStateKind.NO_IDEA
    assert cis.timestamp == ts


def test_some_idea(tmp_path):
    iid = a_idea_id()
    cis = make_cis(tmp_path, "dendritic_hl_idea({})\n".format(iid))
    assert cis.kind == IdeaStateKind.IDEA
    assert cis.idea_id == iid


def test_cruft_lines_ignored(tmp_path):
    ts = ids.now_timestamp()
    text = "garbage\n\ndendritic_hl_root({})\n# a comment\n".format(ts)
    cis = make_cis(tmp_path, text)
    assert cis.kind == IdeaStateKind.NO_IDEA
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
    cis = make_cis(tmp_path, text)
    assert cis.kind == IdeaStateKind.CONFLICT
    assert len(cis.parsed_lines) == 2


def test_conflict_does_not_raise_on_parse_but_has_message(tmp_path):
    """A conflict must not raise on parse; the definite-state consumer
    (Context.current_idea_node) is what raises, using problem_message()."""
    ts1 = ids.now_timestamp()
    ts2 = ids.now_timestamp()
    text = "dendritic_hl_root({})\ndendritic_hl_root({})\n".format(ts1, ts2)
    cis = make_cis(tmp_path, text)
    assert cis.kind == IdeaStateKind.CONFLICT  # no raise
    assert "does not encode a single state" in cis.problem_message()
