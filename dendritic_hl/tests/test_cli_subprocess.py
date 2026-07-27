"""Subprocess tests: real process exit codes and real atexit rollback.

These run ./dh_hl as a child process, so they exercise the actual argparse entry
point, sys.exit codes, real locking, and -- crucially -- the atexit rollback
handler, which only fires at genuine interpreter exit.  The catalog + session
are bootstrapped in-process (the `session` fixture); the child then operates on
them via -C/-s.
"""

import json
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


def test_new_catalog_end_to_end_cli(run_cli, tmp_path):
    """Bootstrap a catalog+session entirely through the real CLI (no in-process
    helper), then drive it -- the highest-fidelity path now that new_catalog
    exists."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "in.cpp").write_text("// gen\n")
    (tmp_path / "p.txt").write_text("explore\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed",
                str(tmp_path / "p.txt"), str(tmp_path / "in.cpp"))
    assert r.returncode == 0, r.stderr
    sid = [l[len("Session: "):] for l in r.stdout.splitlines()
           if l.startswith("Session: ")][0].strip()

    # A handle from list_termini resolves to the same session (shared XDG).
    r = run_cli("list_termini", "-C", cat_dir)
    assert r.returncode == 0 and sid in r.stdout
    handle = [l.split("handle:")[1].strip() for l in r.stdout.splitlines()
              if "handle:" in l][0]
    r = run_cli("status", "-s", handle)
    assert r.returncode == 0
    assert "workspace consistent" in r.stdout
    assert sid in r.stdout


def test_missing_input_file_is_clean_error(run_cli, tmp_path):
    """A missing input file is a clean dh_hl error, not a traceback."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "in.cpp").write_text("// gen\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed",
                str(tmp_path / "nope.txt"), str(tmp_path / "in.cpp"))
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "cannot read input file" in r.stderr


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


def test_review_cancels_cancelled_by_via_real_cli(run_cli, session):
    """Subprocess mirror of test_review.test_cancels_and_cancelled_by: drive the
    real `comment`/`json_schedule_info` CLI, computing the derived review and
    cancelled_by state end-to-end (comment text piped via stdin '-')."""
    sid = run_cli("seed_schedule_full_id", *_cli(session)).stdout.strip()

    def sched_json():
        r = run_cli("json_schedule_info", "-C", session.catalog_dir, sid)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    # A negative comment -> schedule review negative.
    r = run_cli("comment", "-C", session.catalog_dir, "-", sid,
                "--review", "negative", input="regression\n")
    assert r.returncode == 0, r.stderr
    obj = sched_json()
    assert obj["review"] == "negative"
    neg_id = obj["commentary"][0]["id"]

    # A positive comment that CANCELS the negative one -> review flips positive.
    r = run_cli("comment", "-C", session.catalog_dir, "-", sid,
                "--review", "positive", "--cancels", neg_id,
                input="actually fine\n")
    assert r.returncode == 0, r.stderr

    obj = sched_json()
    by_text = {c["text"]: c for c in obj["commentary"]}
    neg, pos = by_text["regression\n"], by_text["actually fine\n"]
    assert pos["cancels"] == [neg_id]
    assert neg["cancelled_by"] == [pos["id"]]
    assert obj["review"] == "positive"

    # An unresolvable --cancels target is a clean error, not a traceback.
    r = run_cli("comment", "-C", session.catalog_dir, "-", sid,
                "--review", "neutral", "--cancels", "root.deadbeef",
                input="bad\n")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
