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

    # new_catalog does NOT initialize the workspace: status says so until
    # init_workspace runs (the blessed initializer), then it's consistent.
    r = run_cli("status", "-s", handle)
    assert r.returncode == 0 and "missing workspace" in r.stdout
    assert "init_workspace" in r.stdout

    r = run_cli("init_workspace", "-s", handle)
    assert r.returncode == 0, r.stderr
    r = run_cli("status", "-s", handle)
    assert r.returncode == 0
    assert "workspace consistent" in r.stdout
    assert sid in r.stdout


def test_catalog_extension_required_only_by_new_catalog(run_cli, tmp_path):
    """new_catalog enforces the `.dh_hl` naming convention, but every other tool
    accepts a catalog directory with any name (idea.md "-C/-s")."""
    (tmp_path / "in.cpp").write_text("// gen\n")
    (tmp_path / "p.txt").write_text("explore\n")

    # new_catalog rejects a directory without the extension.
    r = run_cli("new_catalog", "-C", str(tmp_path / "noext"), "seed",
                str(tmp_path / "p.txt"), str(tmp_path / "in.cpp"))
    assert r.returncode == 1 and "must end with .dh_hl" in r.stderr

    # A normal catalog, renamed to an extensionless path, is still readable by a
    # -C tool (the extension check does NOT run outside new_catalog).
    cat_dir, _ = _bootstrap_cli(run_cli, tmp_path)
    renamed = str(tmp_path / "renamed_catalog")
    os.rename(cat_dir, renamed)
    r = run_cli("list_termini", "-C", renamed)
    assert r.returncode == 0, r.stderr
    assert "handle:" in r.stdout


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
                env={"DENDRITIC_HL_TEST_FAIL_AFTER": "1"}, input="some proposal")
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


def _bootstrap_cli(run_cli, tmp_path):
    """new_catalog + init_workspace through the real CLI; return (cat_dir, handle).
    No Halide needed -- these init_build tests never compile."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "in.cpp").write_text("// gen\n")
    (tmp_path / "p.txt").write_text("explore\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed",
                str(tmp_path / "p.txt"), str(tmp_path / "in.cpp"))
    assert r.returncode == 0, r.stderr
    r = run_cli("list_termini", "-C", cat_dir)
    handle = [l.split("handle:")[1].strip() for l in r.stdout.splitlines()
              if "handle:" in l][0]
    r = run_cli("init_workspace", "-s", handle)
    assert r.returncode == 0, r.stderr
    return cat_dir, handle


def test_new_golden_cli_dispatch_and_exit_codes(run_cli, tmp_path):
    """CLI-layer coverage for the golden tools (idea.md "Golden Object Tools"):
    the `none`-schedule create path, golden_history / json_golden_info dispatch
    and formatting, and the genuine exit codes for the hlpipe gate, a missing
    remarks file, and an unknown golden ID.  Halide-free (never builds)."""
    cat_dir, handle = _bootstrap_cli(run_cli, tmp_path)
    (tmp_path / "rem.txt").write_text("first remarks\n")

    # new_golden with `none` -> creates a golden, prints only its full ID.
    r = run_cli("new_golden", "-s", handle, str(tmp_path / "rem.txt"), "none")
    assert r.returncode == 0, r.stderr
    gid = r.stdout.strip()
    assert gid.startswith("golden_") and "\n" not in gid

    # golden_history dispatch + formatting (no schedule -> "schedule: none").
    r = run_cli("golden_history", "-C", cat_dir)
    assert r.returncode == 0 and "schedule: none" in r.stdout
    assert "first remarks" in r.stdout

    # json_golden_info round-trips the stored shape.
    r = run_cli("json_golden_info", "-C", cat_dir, gid)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"remarks": "first remarks\n", "schedule": None}

    # The hlpipe gate: a real schedule with nothing built -> exit 1, clean error.
    sid = run_cli("schedule_short_id", "-s", handle).stdout.strip()
    r = run_cli("new_golden", "-s", handle, str(tmp_path / "rem.txt"), sid)
    assert r.returncode == 1
    assert "no algorithm hlpipe built" in r.stderr and "Traceback" not in r.stderr

    # Missing remarks file -> exit 1, clean error (not a traceback).
    r = run_cli("new_golden", "-s", handle, str(tmp_path / "nope.txt"), "none")
    assert r.returncode == 1 and "Traceback" not in r.stderr

    # Unknown golden ID -> exit 1, clean error.
    r = run_cli("json_golden_info", "-C", cat_dir,
                "golden_2020-01-01T000000_000000Z")
    assert r.returncode == 1
    assert "no such golden" in r.stderr and "Traceback" not in r.stderr


def test_init_build_positional_target(run_cli, tmp_path):
    """The mistake that bit an agent -- passing the target positionally instead
    of via --target -- now works (idea.md Init-Build Tool `--target` lenience)."""
    cat_dir, handle = _bootstrap_cli(run_cli, tmp_path)
    sid = run_cli("schedule_short_id", "-s", handle).stdout.strip()
    r = run_cli("init_build", "-s", handle, sid, "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    assert "dh_hl: init_build target:" in r.stdout


def test_magic_schedule_ids_wired_into_non_default_args(run_cli, tmp_path):
    """The magic `[schedule ID]` values (idea.md: "All schedule ID arguments
    also accept ...") are wired into the non-`[schedule ID]`-alone argument
    sites, not just the plain default: init_build's --target/--other/--anchor
    and restore_schedule's {schedule ID}.  They all funnel through
    ctx.resolve_schedule_arg.  Halide-free -- init_build never compiles."""
    cat_dir, handle = _bootstrap_cli(run_cli, tmp_path)
    sid = run_cli("schedule_short_id", "-s", handle).stdout.strip()

    def field(stdout, prefix):
        return [l.split(prefix, 1)[1].strip()
                for l in stdout.splitlines() if prefix in l][0]

    # Close the session so `session_output` / `terminus` are defined (the seed
    # canonical, given commentary, is the primary output).
    (tmp_path / "c.txt").write_text("summary\n")
    assert run_cli("comment", "-s", handle, str(tmp_path / "c.txt"),
                   sid).returncode == 0
    assert run_cli("close_session", "-s", handle,
                   "--allow-failed-problems").returncode == 0

    # --target session_output resolves to the closed session's output.
    r = run_cli("init_build", "-s", handle, "--target", "session_output",
                "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    assert field(r.stdout, "dh_hl: init_build target: ") == sid
    # --anchor terminus resolves (the unique terminus's output == that node).
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "none", "--anchor", "terminus")
    assert r.returncode == 0, r.stderr
    assert field(r.stdout, "dh_hl: init_build anchor: ") == sid
    # --other session_output resolves too.
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "session_output", "--anchor", "none")
    assert r.returncode == 0, r.stderr
    assert field(r.stdout, "dh_hl: init_build other: ") == sid

    # restore_schedule {schedule ID} accepts a magic value as well.
    r = run_cli("restore_schedule", "-s", handle, "terminus")
    assert r.returncode == 0, r.stderr
    assert sid in r.stdout


def test_session_output_without_session_is_clean_cli_error(run_cli, tmp_path):
    """`session_output` is a magic [schedule ID] that needs -s.  Passing it to a
    -C-only invocation must be a clean exit-1 dh_hl error that names the argument
    -- NOT a raw Python traceback (context.resolve_schedule_arg `require_session`
    guards the otherwise session-less resolution)."""
    cat_dir, _ = _bootstrap_cli(run_cli, tmp_path)
    r = run_cli("schedule_full_id", "-C", cat_dir, "session_output")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "session_output schedule node argument" in r.stderr


def test_init_build_positional_and_flag_conflict(run_cli, tmp_path):
    """Giving BOTH the positional target and --target is a clean error."""
    cat_dir, handle = _bootstrap_cli(run_cli, tmp_path)
    sid = run_cli("schedule_short_id", "-s", handle).stdout.strip()
    r = run_cli("init_build", "-s", handle, sid, "--target", "workspace")
    assert r.returncode == 1
    assert "not both" in r.stderr
    assert "Traceback" not in r.stderr


def test_failed_init_build_cli_still_invalidates(run_cli, tmp_path):
    """A low-level (argparse) init_build failure still clears an earlier success's
    selection, as long as a resolvable -s was passed -- the pre-argparse guard in
    main() (idea.md Init-Build Tool footgun).  A regression that only invalidated
    inside cmd_init_build would leave the stale selection, and `build` would try
    to compile it instead of reporting the missing selection."""
    cat_dir, handle = _bootstrap_cli(run_cli, tmp_path)
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr

    # argparse rejects the unknown flag (exit 2), but -s is valid, so the
    # pre-parse guard has already cleared the selection.
    r = run_cli("init_build", "-s", handle, "--bogus-flag")
    assert r.returncode == 2

    r = run_cli("build", "-s", handle, "--only", "all")
    assert r.returncode != 0
    assert "no successful init_build" in r.stderr


def test_new_problem_cli_remainder_dispatch_and_exit_codes(run_cli, tmp_path):
    """CLI-layer coverage the in-process problem tests (run_tool bypasses
    argparse) can't give (idea.md new_problem): argparse REMAINDER capture of a
    flag-laden runner command line, real dispatch + list/json formatting, and
    genuine process exit codes for the empty-argv / bad-short-name / bad-<...> /
    duplicate error paths."""
    cat_dir, _ = _bootstrap_cli(run_cli, tmp_path)   # has a `default` main problem

    # REMAINDER captures flag-like tokens and the <RunGenMain> placeholder
    # verbatim (argv distinct from `default` so it is not a duplicate).
    r = run_cli("new_problem", "-C", cat_dir, "bench",
                "<RunGenMain>", "--benchmarks=all")
    assert r.returncode == 0, r.stderr
    # A custom runner carrying <Lib> and a trailing flag.
    r = run_cli("new_problem", "-C", cat_dir, "lib", "./runner", "<Lib>", "-v")
    assert r.returncode == 0, r.stderr

    # Dispatch + list formatting: default + bench + lib, argv round-tripped.
    r = run_cli("list_all_problems", "-C", cat_dir)
    assert r.returncode == 0 and r.stdout.count("id: ") == 3
    assert 'cli: ["./runner", "<Lib>", "-v"]' in r.stdout

    # json_problem_info round-trips the flag-laden argv exactly.
    r = run_cli("json_problem_info", "-C", cat_dir, "problem.bench")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {
        "argv": ["<RunGenMain>", "--benchmarks=all"],
        "state": "enabled", "short_name": "bench"}

    # Empty REMAINDER -> exit 1, clean error.
    r = run_cli("new_problem", "-C", cat_dir, "noargs")
    assert r.returncode == 1
    assert "at least one" in r.stderr and "Traceback" not in r.stderr

    # Bad short name (space) with VALID argv -> exit 1, clean error.
    r = run_cli("new_problem", "-C", cat_dir, "bad name", "<RunGenMain>")
    assert r.returncode == 1
    assert "short name" in r.stderr and "Traceback" not in r.stderr

    # Bad <...> placeholder with VALID short name -> exit 1, clean error.
    r = run_cli("new_problem", "-C", cat_dir, "bogus", "<Bogus>")
    assert r.returncode == 1
    assert "unknown special argument" in r.stderr and "Traceback" not in r.stderr

    # Duplicate argv -> exit 1, names the existing problem, no traceback.
    r = run_cli("new_problem", "-C", cat_dir, "dup", "./runner", "<Lib>", "-v")
    assert r.returncode == 1
    assert "already exists" in r.stderr and "problem.lib" in r.stderr
    assert "Traceback" not in r.stderr


def test_review_cancels_cancelled_by_via_real_cli(run_cli, session):
    """Subprocess mirror of test_review.test_cancels_and_cancelled_by: drive the
    real `comment`/`json_schedule_info` CLI, computing the derived review and
    cancelled_by state end-to-end (comment text piped via stdin '-')."""
    sid = run_cli("schedule_full_id", *_cli(session)).stdout.strip()

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
