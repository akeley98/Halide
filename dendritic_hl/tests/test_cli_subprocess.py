"""Subprocess tests: real process exit codes and real atexit rollback.

These run ./dh_hl as a child process, so they exercise the actual argparse
entry point, sys.exit codes, and -- crucially -- the atexit rollback handler,
which only fires at genuine interpreter exit.
"""

import os

import pytest

from conftest import DUMMY_SOURCE


def _snapshot(root):
    """Map of relpath -> bytes for every file under *root*."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = f.read()
    return out


def test_help_exit_zero(run_cli):
    r = run_cli("help")
    assert r.returncode == 0
    assert "dh_hl commands" in r.stdout


def test_status_missing_workspace_errors(run_cli, tmp_path):
    r = run_cli("status", str(tmp_path / "nope.cpp"))
    assert r.returncode == 1
    assert "does not exist" in r.stderr


def test_new_root_exit_zero(run_cli, workspace):
    r = run_cli("new_root", str(workspace))
    assert r.returncode == 0
    assert "Created root schedule" in r.stdout
    assert os.path.isdir(str(workspace) + ".dh_hl")


def test_new_idea_bad_proposal_name_exit_one(run_cli, workspace):
    run_cli("new_root", str(workspace))
    r = run_cli("new_idea", str(workspace), "has spaces", "-", input="txt")
    assert r.returncode == 1
    assert "proposal name" in r.stderr


def test_atexit_rollback_restores_partial_mutation(run_cli, workspace):
    """With the injected failure, new_idea dies mid-flush; the catalog must be
    byte-for-byte what it was before the failed command."""
    run_cli("new_root", str(workspace))
    cat_dir = str(workspace) + ".dh_hl"
    before = _snapshot(cat_dir)

    # new_idea writes proposal.txt (a new file); fail on the 1st new file.
    r = run_cli("new_idea", str(workspace), "vec", "-",
                env={"DH_HL_TEST_FAIL_AFTER": "1"}, input="some proposal")
    assert r.returncode != 0

    after = _snapshot(cat_dir)
    assert after == before, "atexit rollback did not fully restore the catalog"
    # Specifically, no stray idea directory survived.
    assert not os.path.isdir(os.path.join(cat_dir, "idea")) or \
        os.listdir(os.path.join(cat_dir, "idea")) == []


def test_new_idea_proposal_from_stdin(run_cli, workspace):
    """The `-` = stdin convention, as an agent would pipe it via <<EOF."""
    run_cli("new_root", str(workspace))
    r = run_cli("new_idea", str(workspace), "vec", "-",
                input="Vectorize wider.\nMore detail.\n")
    assert r.returncode == 0
    idea_root = os.path.join(str(workspace) + ".dh_hl", "idea")
    assert len(os.listdir(idea_root)) == 1


def test_successful_command_is_not_rolled_back(run_cli, workspace):
    """Sanity: without injection, the new idea persists (commit disarmed it)."""
    run_cli("new_root", str(workspace))
    r = run_cli("new_idea", str(workspace), "vec", "-", input="proposal text")
    assert r.returncode == 0
    idea_root = os.path.join(str(workspace) + ".dh_hl", "idea")
    assert os.path.isdir(idea_root) and len(os.listdir(idea_root)) == 1
