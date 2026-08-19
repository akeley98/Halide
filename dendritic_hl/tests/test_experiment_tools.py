"""Tests for the throwaway `dh_hl experiment` tools.

These live in their own file (per idea.md) so the whole experiment feature can
be deleted in one sweep once the LLM Halide scheduling experiment is done.

Covers the sub-actions: begin / get_begin_label / get_begin_timestamp / time
(catalog-level write-once state under `experiment/`), add_schedule_node (a
workspace-free root schedule creator that silent-dedups on content hash and
takes optional EXPERIMENT IGNORE commentary), json_test_schedules (success
schedules with no active EXPERIMENT IGNORE commentary), and build_external (a
catalog-free compile taking an explicit Halide path).
"""

import json
import os
import re
import shutil

import pytest

from dendritic_hl_lib import guide_flag, ids, safety, tools
from dendritic_hl_lib.enums import Result, Review
from dendritic_hl_lib.errors import DhHlError

from conftest import _PKG_ROOT, HALIDE_BUILD_DIR, HALIDE_DIR, open_catalog


def _xp(session, action, arg1=None, arg2=None, arg3=None, ignore=None):
    """Namespace for cmd_experiment with all argparse-provided fields set."""
    return session.ns(action=action, arg1=arg1, arg2=arg2, arg3=arg3,
                      ignore=ignore)


def _begin(run_tool, session, label):
    run_tool(tools.cmd_experiment, _xp(session, "begin", arg1=label))


# ---------------------------------------------------------------------------
# begin / get_begin_label / get_begin_timestamp
# ---------------------------------------------------------------------------

def test_begin_records_label_and_timestamp(run_tool, session, capsys):
    _begin(run_tool, session, "harness_F_guide_F")
    exp_dir = os.path.join(session.catalog_dir, "experiment")
    assert os.path.isfile(os.path.join(exp_dir, "label.txt"))
    assert os.path.isfile(os.path.join(exp_dir, "begin_timestamp.txt"))

    capsys.readouterr()  # discard anything from begin
    run_tool(tools.cmd_experiment, _xp(session, "get_begin_label"))
    assert capsys.readouterr().out == "harness_F_guide_F\n"

    run_tool(tools.cmd_experiment, _xp(session, "get_begin_timestamp"))
    ts = capsys.readouterr().out.rstrip("\n")
    assert ids.is_timestamp(ts)


def test_begin_twice_fails(run_tool, session):
    _begin(run_tool, session, "harness_F_guide_F")
    with pytest.raises(DhHlError, match="already been called"):
        _begin(run_tool, session, "harness_F_guide_F")


def test_begin_rejects_unknown_label(run_tool, session):
    with pytest.raises(DhHlError, match="must be one of"):
        _begin(run_tool, session, "not_a_real_label")


def test_get_before_begin_fails(run_tool, session):
    with pytest.raises(DhHlError, match="has not been called"):
        run_tool(tools.cmd_experiment, _xp(session, "get_begin_label"))
    with pytest.raises(DhHlError, match="has not been called"):
        run_tool(tools.cmd_experiment, _xp(session, "get_begin_timestamp"))


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------

def test_time_prints_elapsed_seconds(run_tool, session, capsys):
    _begin(run_tool, session, "harness_F_guide_F")
    capsys.readouterr()  # discard anything from begin
    run_tool(tools.cmd_experiment, _xp(session, "time"))
    out = capsys.readouterr().out
    # The ONLY stdout is the number + newline, in microsecond precision.
    assert re.fullmatch(r"\d+\.\d{6}\n", out), repr(out)
    assert float(out) >= 0.0


def test_time_before_begin_fails(run_tool, session):
    with pytest.raises(DhHlError, match="has not been called"):
        run_tool(tools.cmd_experiment, _xp(session, "time"))


# ---------------------------------------------------------------------------
# begin guide-state assertions
# ---------------------------------------------------------------------------

@pytest.fixture
def guide_state():
    """Set guide_flag.enabled for a test, restoring it afterward."""
    original = guide_flag.enabled

    def _set(value):
        guide_flag.enabled = value
    yield _set
    guide_flag.enabled = original


@pytest.mark.parametrize("enabled,label,ok", [
    (True, "harness_T_guide_T", True),
    (True, "harness_T_guide_F", False),
    (False, "harness_T_guide_F", True),
    (False, "harness_T_guide_T", False),
    # harness_F_* never asserts, regardless of guide state.
    (True, "harness_F_guide_F", True),
    (False, "harness_F_guide_T", True),
])
def test_begin_guide_assertions(run_tool, session, guide_state, enabled, label, ok):
    guide_state(enabled)
    if ok:
        _begin(run_tool, session, label)
        assert os.path.isfile(
            os.path.join(session.catalog_dir, "experiment", "label.txt"))
    else:
        with pytest.raises(DhHlError, match="guide is"):
            _begin(run_tool, session, label)
        # A failed assertion writes nothing.
        assert not os.path.exists(
            os.path.join(session.catalog_dir, "experiment"))


# ---------------------------------------------------------------------------
# add_schedule_node
# ---------------------------------------------------------------------------

def _write_gen(tmp_path, source="// gen\n", params="[{}]"):
    src = tmp_path / "gen.cpp"
    prm = tmp_path / "params.json"
    src.write_text(source)
    prm.write_text(params)
    return str(src), str(prm)


def _add_node(run_tool, session, tmp_path, capsys, source="// gen\n",
              params="[{}]", ignore=None):
    src, prm = _write_gen(tmp_path, source, params)
    capsys.readouterr()
    run_tool(tools.cmd_experiment,
             _xp(session, "add_schedule_node", arg1=src, arg2=prm, ignore=ignore))
    out = capsys.readouterr().out
    assert out.endswith("\n")
    full_id = out.strip()
    assert "\n" not in full_id  # only the full ID, nothing else
    return full_id


def test_add_schedule_node_creates_root(run_tool, session, tmp_path, capsys):
    full_id = _add_node(run_tool, session, tmp_path, capsys, source="// A\n")
    cat = open_catalog(session.catalog_dir)
    node = cat.get_schedule(full_id)
    assert node.is_root()
    assert node.is_major()
    # Created for use after a successful build_external, so recorded as success.
    assert node.result == Result.SUCCESS
    assert node.source == "// A\n"


def test_add_schedule_node_dedup(run_tool, session, tmp_path, capsys):
    """Identical content (source + params) twice returns the SAME node -- a
    silent no-op collision, so profiler_session.py never measures a schedule
    twice."""
    a = _add_node(run_tool, session, tmp_path, capsys, source="// same\n")
    b = _add_node(run_tool, session, tmp_path, capsys, source="// same\n")
    assert a == b


def test_add_schedule_node_dedup_keys_on_params(run_tool, session, tmp_path, capsys):
    """The content hash covers BOTH files, so the same source with different
    generator parameters is a distinct node."""
    a = _add_node(run_tool, session, tmp_path, capsys, source="// s\n",
                  params="[{}]")
    b = _add_node(run_tool, session, tmp_path, capsys, source="// s\n",
                  params='[{"pyramid_levels": 4}]')
    assert a != b


def test_add_schedule_node_dedup_does_not_reignore(
        run_tool, session, tmp_path, capsys):
    """A dedup hit adds no --ignore commentary, so a later call can never
    retroactively hide an existing non-ignored node."""
    first = _add_node(run_tool, session, tmp_path, capsys, source="// keep\n")
    second = _add_node(run_tool, session, tmp_path, capsys, source="// keep\n",
                       ignore=["too late"])
    assert second == first
    cat = open_catalog(session.catalog_dir)
    node = cat.get_schedule(first)
    assert not any(c.text.startswith("EXPERIMENT IGNORE:")
                   for c in node.commentary)


def test_add_schedule_node_ignore_commentary(run_tool, session, tmp_path, capsys):
    full_id = _add_node(run_tool, session, tmp_path, capsys,
                        ignore=["reason one", "reason two"])
    cat = open_catalog(session.catalog_dir)
    node = cat.get_schedule(full_id)
    ignores = [c for c in node.commentary
               if c.text.startswith("EXPERIMENT IGNORE:")]
    assert sorted(c.text for c in ignores) == [
        "EXPERIMENT IGNORE: reason one",
        "EXPERIMENT IGNORE: reason two",
    ]
    assert all(c.review == Review.NEGATIVE for c in ignores)


# ---------------------------------------------------------------------------
# json_test_schedules
# ---------------------------------------------------------------------------

def _json_test_schedules(run_tool, session, capsys):
    capsys.readouterr()
    run_tool(tools.cmd_experiment, _xp(session, "json_test_schedules"))
    return json.loads(capsys.readouterr().out)


def test_json_test_schedules_lists_successes_excluding_ignored(
        run_tool, session, tmp_path, capsys):
    plain = _add_node(run_tool, session, tmp_path, capsys, source="// plain\n")
    ignored = _add_node(run_tool, session, tmp_path, capsys, source="// hide\n",
                        ignore=["dead end"])

    result = _json_test_schedules(run_tool, session, capsys)
    assert plain in result
    assert ignored not in result
    # The listing is exactly the success + non-ignored nodes.  The fixture's seed
    # root + canonical are majors but their result is still `unknown`, so they are
    # NOT listed (the criterion is success, not majorness).
    cat = open_catalog(session.catalog_dir)
    non_success = [n.full_id for n in cat.schedules.values()
                   if n.result != Result.SUCCESS]
    assert non_success  # the fixture roots are unbuilt
    assert set(non_success).isdisjoint(result)


def test_json_test_schedules_cancelled_ignore_reappears(
        run_tool, session, tmp_path, capsys):
    ignored = _add_node(run_tool, session, tmp_path, capsys, ignore=["oops"])
    assert ignored not in _json_test_schedules(run_tool, session, capsys)

    # Cancel the EXPERIMENT IGNORE commentary; the node becomes a test schedule
    # again (json_test_schedules counts only non-cancelled commentary).
    cat = open_catalog(session.catalog_dir)
    try:
        node = cat.get_schedule(ignored)
        ign = next(c for c in node.commentary
                   if c.text.startswith("EXPERIMENT IGNORE:"))
        node.add_commentary("undo the ignore", cancels=[ign.local_id])
        cat.flush()
        safety.commit()
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()

    assert ignored in _json_test_schedules(run_tool, session, capsys)


# ---------------------------------------------------------------------------
# Real subprocess: argparse choices, exit codes, write-once, guide env var.
# ---------------------------------------------------------------------------

def test_cli_roundtrip_and_write_once(run_cli, session):
    r = run_cli("experiment", "-C", session.catalog_dir, "begin",
                "harness_F_guide_F")
    assert r.returncode == 0, r.stderr

    r = run_cli("experiment", "-C", session.catalog_dir, "get_begin_label")
    assert r.returncode == 0 and r.stdout == "harness_F_guide_F\n"

    # A second begin fails (write-once) and leaves the recorded label untouched.
    r = run_cli("experiment", "-C", session.catalog_dir, "begin",
                "harness_F_guide_T")
    assert r.returncode != 0
    r = run_cli("experiment", "-C", session.catalog_dir, "get_begin_label")
    assert r.stdout == "harness_F_guide_F\n"


def test_cli_rejects_unknown_action(run_cli, session):
    r = run_cli("experiment", "-C", session.catalog_dir, "bogus")
    assert r.returncode != 0
    assert "invalid choice" in r.stderr


def test_cli_guide_env_assertion(run_cli, session):
    # Guide forced on: harness_T_guide_F must fail its assertion.
    r = run_cli("experiment", "-C", session.catalog_dir, "begin",
                "harness_T_guide_F", env={"DENDRITIC_HL_GUIDE_ENABLED": "1"})
    assert r.returncode != 0
    # Guide forced off: harness_T_guide_T must fail its assertion.
    r = run_cli("experiment", "-C", session.catalog_dir, "begin",
                "harness_T_guide_T", env={"DENDRITIC_HL_GUIDE_ENABLED": "0"})
    assert r.returncode != 0
    # Nothing was recorded by the failed attempts.
    r = run_cli("experiment", "-C", session.catalog_dir, "get_begin_label")
    assert r.returncode != 0

    # Guide forced on with the matching label succeeds.
    r = run_cli("experiment", "-C", session.catalog_dir, "begin",
                "harness_T_guide_T", env={"DENDRITIC_HL_GUIDE_ENABLED": "1"})
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# build_external: catalog-free compile (no -C).  Validation paths need no
# Halide; the real build is opt-in (marked `halide`).
# ---------------------------------------------------------------------------

def test_cli_build_external_needs_four_args(run_cli, tmp_path):
    src = tmp_path / "g.cpp"; src.write_text("// x\n")
    prm = tmp_path / "p.json"; prm.write_text("[{}]")
    # bin dir but no Halide path.
    r = run_cli("experiment", "build_external", str(src), str(prm),
                str(tmp_path / "bin"))
    assert r.returncode != 0
    assert "requires a generator" in r.stderr


def test_cli_build_external_missing_source_is_clean_error(run_cli, tmp_path):
    prm = tmp_path / "p.json"; prm.write_text("[{}]")
    r = run_cli("experiment", "build_external", str(tmp_path / "nope.cpp"),
                str(prm), str(tmp_path / "bin"), HALIDE_DIR)
    assert r.returncode != 0
    assert "no such generator" in r.stderr and "Traceback" not in r.stderr


def test_cli_build_external_empty_params_is_clean_error(run_cli, tmp_path):
    src = tmp_path / "g.cpp"; src.write_text("// x\n")
    prm = tmp_path / "p.json"; prm.write_text("[]")
    r = run_cli("experiment", "build_external", str(src), str(prm),
                str(tmp_path / "bin"), HALIDE_DIR)
    assert r.returncode != 0
    assert "empty" in r.stderr and "Traceback" not in r.stderr


@pytest.mark.halide
@pytest.mark.skipif(not os.path.isdir(HALIDE_BUILD_DIR),
                    reason="no local Halide build at " + HALIDE_BUILD_DIR)
@pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not found")
def test_cli_build_external_real_build_numbers_params(run_cli, tmp_path):
    """A real build (no -C, explicit Halide path): each generator-params
    object lands in its own numbered subdir (0, 1), and no output name embeds a
    catalog full_id."""
    histp = os.path.join(_PKG_ROOT, "tests", "hist_params.cpp")
    prm = tmp_path / "p.json"
    prm.write_text('[{}, {"enable_parallel": true}]')
    bin_dir = tmp_path / "bin"
    r = run_cli("experiment", "build_external", histp, str(prm), str(bin_dir),
                HALIDE_DIR)
    assert r.returncode == 0, r.stderr
    for i in ("0", "1"):
        assert (bin_dir / i / "dh_hl_pipeline.rungen").is_file(), i
        assert (bin_dir / i / "dh_hl_pipeline.h").is_file(), i
    # Param-independent outputs use the neutral base, not a catalog full_id.
    assert (bin_dir / "external_generator").is_file()
    assert (bin_dir / "external.ninja").is_file()
    assert (bin_dir / "RunGenMain.o").is_file()
    import re
    # No bin/ entry embeds a catalog full_id (a "<timestamp>_<hash>" string).
    assert not any(re.search(r"\d{4}-\d\d-\d\dT\d", n)
                   for n in os.listdir(str(bin_dir)))
