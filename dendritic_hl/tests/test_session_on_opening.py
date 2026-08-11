"""Session "on opening" snapshots (idea.md "Session Node State" / "Session
Creation Tools"): a session records the golden schedule node and the enabled
problems as they were the instant it was created."""

import json
import os

from dendritic_hl_lib import locks, safety, tools
from conftest import open_catalog


def _reset():
    locks._reset_for_tests()


def _session_json(run_tool, capsys, session):
    run_tool(tools.cmd_json_session_info, session.ns())
    return json.loads(capsys.readouterr().out)


def test_fixture_session_records_default_problem_and_no_golden(
        session, run_tool, capsys):
    """The seeded catalog creates the `default` problem before the session, so
    the session's enabled-problems-on-opening is exactly [default], and (no
    golden was added) golden-on-opening is null."""
    cat = open_catalog(session.catalog_dir)
    try:
        default_id = cat.main_problem().full_id
        node = cat.get_session(session.session_id)
        assert node.enabled_problem_ids_on_opening == [default_id]
        assert node.golden_schedule_id_on_opening is None
    finally:
        _reset()

    j = _session_json(run_tool, capsys, session)
    assert j["enabled_problems_on_opening"] == [default_id]
    assert j["golden_schedule_on_opening"] is None


def test_snapshot_reflects_state_at_creation_time(session, run_tool):
    """create_session snapshots the CURRENT catalog state: a problem or golden
    added AFTER the session is not retroactively part of its on-opening set; a
    session created afterwards DOES see them."""
    cat = open_catalog(session.catalog_dir)
    try:
        first = cat.get_session(session.session_id)
        default_id = cat.main_problem().full_id
        # Add a second problem and a golden AFTER the first session exists.
        p2 = cat.create_problem(["<RunGenMain>", "--extra"], "extra")
        root = [s for s in cat.schedules.values() if s.is_root()][0]
        cat.create_golden("g\n", root.full_id)
        # A brand-new session created now (reusing the seed idea) snapshots the
        # richer state.
        seed = cat.get_idea(first.seed_idea_id)
        later = cat.create_session(seed, None, 0)
        cat.flush(); safety.commit()

        # First session unchanged.
        assert first.enabled_problem_ids_on_opening == [default_id]
        assert first.golden_schedule_id_on_opening is None
        # Later session saw both new problem and the golden.
        assert set(later.enabled_problem_ids_on_opening) == {default_id, p2.full_id}
        assert later.golden_schedule_id_on_opening == root.full_id
        later_id = later.full_id
    finally:
        _reset()

    # Round-trips from disk.
    cat2 = open_catalog(session.catalog_dir)
    try:
        reloaded = cat2.get_session(later_id)
        assert reloaded.golden_schedule_id_on_opening == \
            [s for s in cat2.schedules.values() if s.is_root()][0].full_id
        assert len(reloaded.enabled_problem_ids_on_opening) == 2
        # The on-disk files exist.
        d = reloaded.dir
        assert os.path.isfile(os.path.join(d, "golden_on_opening.txt"))
        assert os.path.isfile(os.path.join(d, "enabled_problems_on_opening.json"))
    finally:
        _reset()


def test_legacy_session_missing_files_reads_empty(session):
    """A session dir predating the snapshot (no on-opening files) reads as no
    golden + empty enabled-problems, not a crash."""
    cat = open_catalog(session.catalog_dir)
    try:
        node = cat.get_session(session.session_id)
        d = node.dir
    finally:
        _reset()
    os.remove(os.path.join(d, "enabled_problems_on_opening.json"))
    # golden_on_opening.txt was never written (no golden), already absent.
    cat2 = open_catalog(session.catalog_dir)
    try:
        node = cat2.get_session(session.session_id)
        assert node.enabled_problem_ids_on_opening == []
        assert node.golden_schedule_id_on_opening is None
    finally:
        _reset()
