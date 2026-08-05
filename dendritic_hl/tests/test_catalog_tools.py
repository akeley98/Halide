"""Integration tests for the non-build tools, driven through cmd_* in-process.

No Halide required: these tools only touch the on-disk catalog.  Each cmd_* is
run via `run_tool`, which resets + re-arms the (fake) lock state per call to
model the once-per-process lock lifecycle.  The `session` fixture provides a
catalog with one consistent top-level session (root -> seed idea -> canonical
duplicate; the private workspace holds DUMMY_SOURCE pointing at the seed idea).
"""

import json

import pytest

from dendritic_hl_lib import safety, tools
from dendritic_hl_lib.errors import DhHlError
from conftest import ns, open_catalog


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _status(session, run_tool, capsys):
    run_tool(tools.cmd_status, session.ns())
    return capsys.readouterr().out


def _schedule_id_from_status(status_out):
    for line in status_out.splitlines():
        if line.startswith("Schedule node:"):
            return line.split()[-1]
    raise AssertionError("no consistent schedule node in status:\n" + status_out)


def test_seeded_session_status_consistent(session, run_tool, capsys):
    out = _status(session, run_tool, capsys)
    assert "workspace consistent" in out
    assert "Session: " in out


def test_status_skips_session_lock(session, run_tool, capsys):
    """status is read-only: it takes the catalog lock but NOT the session lock."""
    from dendritic_hl_lib import locks
    run_tool(tools.cmd_status, session.ns())
    assert locks._trace_sink == [("machine", "shared"), ("catalog", "exclusive")]


def test_list_ideas_skip_session_lock(session, run_tool):
    """list_child_ideas / list_seed_ideas are read-only (idea.md): catalog lock
    only, never the session lock -- verified via the lock trace."""
    from dendritic_hl_lib import locks
    lock_free = [("machine", "shared"), ("catalog", "exclusive")]
    run_tool(tools.cmd_list_child_ideas, session.ns())
    assert locks._trace_sink == lock_free
    run_tool(tools.cmd_list_seed_ideas, session.ns())
    assert locks._trace_sink == lock_free


def test_new_root_takes_session_lock(session, run_tool, capsys):
    """A mutating -s tool takes the session lock before the catalog lock."""
    from dendritic_hl_lib import locks
    session.write_workspace("fresh source\n")
    run_tool(tools.cmd_new_root, session.ns())
    assert locks._trace_sink == [
        ("machine", "shared"), ("session", "exclusive"), ("catalog", "exclusive")]


def test_new_root_then_status_consistent(session, run_tool, capsys):
    session.write_workspace("brand new root source\n")
    run_tool(tools.cmd_new_root, session.ns())
    assert "Created root schedule" in capsys.readouterr().out

    out = _status(session, run_tool, capsys)
    assert "workspace consistent" in out
    assert "no current idea" in out


def test_new_root_rejects_existing_major(session, run_tool):
    # The seeded workspace already matches the seed idea's canonical (a major).
    with pytest.raises(DhHlError, match="already stored as a major schedule"):
        run_tool(tools.cmd_new_root, session.ns())


def test_idea_lifecycle(session, run_tool, tmp_path, capsys):
    prop = _write(tmp_path, "prop.txt", "Vectorize wider.\nSecond line.\n")
    run_tool(tools.cmd_new_idea,
             session.ns(proposal_name="vec_wider", proposal=prop))
    assert "Created idea" in capsys.readouterr().out

    run_tool(tools.cmd_list_child_ideas, session.ns())  # default schedule = unambiguous
    listing = capsys.readouterr().out
    assert "proposal: vec_wider" in listing      # prefixed proposal line
    assert "canonical: " in listing              # prefixed canonical line
    assert "Vectorize wider." in listing
    assert "Second line." not in listing  # only first line, truncated

    run_tool(tools.cmd_view_idea, session.ns(idea=".vec_wider"))
    assert "vec_wider" in capsys.readouterr().out

    run_tool(tools.cmd_json_idea_info, session.ns(idea=".vec_wider"))
    obj = json.loads(capsys.readouterr().out)
    assert obj["proposal_name"] == "vec_wider"
    assert obj["canonical_schedule"] is None
    assert obj["review"] == "neutral"  # no canonical schedule -> neutral
    assert obj["idea_side_links"] == []
    assert "importance" not in obj


def test_duplicate_proposal_name_rejected(session, run_tool, tmp_path):
    prop = _write(tmp_path, "p.txt", "text")
    run_tool(tools.cmd_new_idea, session.ns(proposal_name="dupname", proposal=prop))
    with pytest.raises(DhHlError, match="already used"):
        run_tool(tools.cmd_new_idea,
                 session.ns(proposal_name="dupname", proposal=prop))


def test_comment_shows_up_in_json(session, run_tool, tmp_path, capsys):
    cfile = _write(tmp_path, "c.txt", "a remark")
    run_tool(tools.cmd_comment,
             session.ns(commentary=cfile, review="positive"))
    capsys.readouterr()  # discard the "Added commentary" line
    run_tool(tools.cmd_json_schedule_info, session.ns())
    obj = json.loads(capsys.readouterr().out)
    assert len(obj["commentary"]) == 1
    assert obj["commentary"][0]["review"] == "positive"
    assert obj["commentary"][0]["text"] == "a remark"
    assert obj["commentary"][0]["cancels"] == []
    assert obj["commentary"][0]["cancelled_by"] == []
    assert obj["review"] == "positive"  # derived from the one positive comment


def test_restore_roundtrips_workspace(session, run_tool, capsys):
    sid = _schedule_id_from_status(_status(session, run_tool, capsys))
    original = open(session.workspace_path, encoding="utf-8").read()

    session.write_workspace(original + "\n// scratch edit\n")
    assert open(session.workspace_path, encoding="utf-8").read() != original

    run_tool(tools.cmd_restore_schedule, session.ns(schedule=sid))
    assert open(session.workspace_path, encoding="utf-8").read() == original

    # After restoring, status should be consistent again.
    assert "workspace consistent" in _status(session, run_tool, capsys)


def test_missing_catalog_errors(session, run_tool, tmp_path):
    """A -C tool pointed at a nonexistent catalog errors cleanly."""
    bogus = str(tmp_path / "nope.dh_hl")
    with pytest.raises(DhHlError, match="no catalog directory"):
        run_tool(tools.cmd_view_idea,
                 tools_ns_catalog(bogus, idea=".seed"))


def test_edited_workspace_is_inconsistent(session, run_tool, capsys):
    session.write_workspace("totally unknown content\n")
    out = _status(session, run_tool, capsys)
    assert "inconsistent, unknown schedule" in out
    # The dramatic "workspace inconsistent" AGENTS banner was intentionally
    # removed (idea.md Status Tool); only the missing-files case advises now.
    assert "AGENTS:" not in out


def test_view_benchmark_stdout(tmp_path, run_tool, capsys):
    """view_benchmark_stdout prints the `stdout` stored on the benchmark verbatim
    (a fabricated benchmark, so no Halide needed)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    captured = "histp  BEST_TIME_MSEC_PER_ITER  1.5\nBest output throughput ...\n"
    bench = R.add_benchmark("Testbox", {
        "hostname": "Testbox", "cpu_count": 4, "parameters": {},
        "profiler": {"name": "p"}, "warnings": [], "stdout": captured})
    cat.flush()
    safety.commit()

    run_tool(tools.cmd_view_benchmark_stdout,
             ns(catalog=cat_dir, benchmark=bench.full_id))
    assert capsys.readouterr().out == captured


def test_view_benchmark_stdout_missing_defaults_empty(tmp_path, run_tool, capsys):
    """A pre-stdout benchmark (no `stdout` key) prints nothing, not a crash
    (idea.md "Benchmark Sub-object State": absent stdout defaults to "")."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    bench = R.add_benchmark("Testbox", {
        "hostname": "Testbox", "cpu_count": 4, "parameters": {},
        "profiler": {"name": "p"}, "warnings": []})
    cat.flush()
    safety.commit()

    run_tool(tools.cmd_view_benchmark_stdout,
             ns(catalog=cat_dir, benchmark=bench.full_id))
    assert capsys.readouterr().out == ""


def tools_ns_catalog(catalog_dir, **kw):
    from conftest import ns
    kw.setdefault("catalog", catalog_dir)
    return ns(**kw)


def test_default_schedule_arg_without_session_errors_clearly(session, run_tool):
    """A -C-only tool with the [schedule ID] omitted needs -s to resolve the
    default (the session workspace's schedule).  The error names the argument
    rather than leaking the generic workspace 'need -s' message (idea.md /
    impl.md "Default [schedule ID] argument")."""
    with pytest.raises(DhHlError,
                       match="resolve the default schedule node argument"):
        run_tool(tools.cmd_view_generator_parameters,
                 ns(catalog=session.catalog_dir, session=None, schedule=None))


def test_explicit_schedule_arg_without_session_ok(session, run_tool, capsys):
    """The same -C-only tool needs no -s when given an explicit [schedule ID]:
    it resolves against the catalog and never touches the session workspace."""
    cat = open_catalog(session.catalog_dir)
    try:
        sid = next(iter(cat.schedules))
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()
    run_tool(tools.cmd_view_generator_parameters,
             ns(catalog=session.catalog_dir, session=None, schedule=sid))
    assert capsys.readouterr().out.strip()
