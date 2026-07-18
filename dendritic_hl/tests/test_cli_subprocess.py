"""Subprocess tests: real process exit codes and real atexit rollback.

These run ./dh_hl as a child process, so they exercise the actual argparse entry
point, sys.exit codes, real locking, and -- crucially -- the atexit rollback
handler, which only fires at genuine interpreter exit.  The catalog + session
are bootstrapped in-process (the `session` fixture); the child then operates on
them via -C/-s.
"""

import os


def _snapshot(root):
    """Map of relpath -> bytes for every file under *root*, excluding the
    gitignored private/ workspace tree (session locks etc. are infra, not
    catalog graph state)."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        if "private" in dirs:
            dirs.remove("private")
        for name in files:
            p = os.path.join(dirpath, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = f.read()
    return out


def _cli(session):
    return ["-s", session.session_id, "-C", session.catalog_dir]


def test_help_exit_zero(run_cli):
    r = run_cli("help")
    assert r.returncode == 0
    assert "dh_hl commands" in r.stdout


def test_status_bad_session_handle_errors(run_cli):
    r = run_cli("status", "-s", "tmp.deadbeef")
    assert r.returncode == 1
    assert "handle" in r.stderr.lower()


def test_new_root_exit_zero(run_cli, session):
    session.write_workspace("fresh root source\n")
    r = run_cli("new_root", *_cli(session))
    assert r.returncode == 0
    assert "Created root schedule" in r.stdout


def test_new_idea_bad_proposal_name_exit_one(run_cli, session):
    r = run_cli("new_idea", *_cli(session), "has spaces", "-", input="txt")
    assert r.returncode == 1
    assert "proposal name" in r.stderr


def test_atexit_rollback_restores_partial_mutation(run_cli, session):
    """With the injected failure, new_idea dies mid-flush; the catalog graph
    must be byte-for-byte what it was before the failed command."""
    cat_dir = session.catalog_dir
    before = _snapshot(cat_dir)

    r = run_cli("new_idea", *_cli(session), "vec", "-",
                env={"DH_HL_TEST_FAIL_AFTER": "1"}, input="some proposal")
    assert r.returncode != 0

    after = _snapshot(cat_dir)
    assert after == before, "atexit rollback did not fully restore the catalog"


def test_new_idea_proposal_from_stdin(run_cli, session):
    """The `-` = stdin convention, as an agent would pipe it via <<EOF."""
    idea_root = os.path.join(session.catalog_dir, "idea")
    before = set(os.listdir(idea_root))
    r = run_cli("new_idea", *_cli(session), "vecwide", "-",
                input="Vectorize wider.\nMore detail.\n")
    assert r.returncode == 0
    after = set(os.listdir(idea_root))
    new = after - before
    assert len(new) == 1 and next(iter(new)).startswith("vecwide_")


def test_successful_command_is_not_rolled_back(run_cli, session):
    """Sanity: without injection, the new idea persists (commit disarmed it)."""
    idea_root = os.path.join(session.catalog_dir, "idea")
    before = len(os.listdir(idea_root))
    r = run_cli("new_idea", *_cli(session), "vecwide", "-", input="proposal text")
    assert r.returncode == 0
    assert len(os.listdir(idea_root)) == before + 1
