"""The no-harness DRM: when allow_harness_flag is off (DENDRITIC_HL_ALLOW_HARNESS
=0), the CLI exposes ONLY the allowlist (main._NO_HARNESS_ALLOWLIST) and turns
every other tool off with a clear message.

Exercised through real subprocesses (run_cli), because the flag -- like guide_flag
-- is read from the environment at import time.  The default (flag on) is the
normal harness, so the rest of the suite is unaffected.
"""

from dendritic_hl_lib import main

_OFF = {"DENDRITIC_HL_ALLOW_HARNESS": "0"}


def test_flag_on_by_default_status_not_blocked(run_cli):
    """With the flag unset (default on) a normal tool is NOT blocked -- it fails
    for its own reason (missing -C/-s), not the DRM."""
    r = run_cli("status")
    assert "turned OFF" not in r.stderr


def test_blocked_command_off(run_cli):
    r = run_cli("status", env=_OFF)
    assert r.returncode == 2
    assert r.stdout == ""
    assert "'status' tool is turned OFF" in r.stderr


def test_prompt_is_blocked_off(run_cli):
    r = run_cli("prompt", "--main", env=_OFF)
    assert r.returncode == 2
    assert r.stdout == ""
    assert "turned OFF" in r.stderr


def test_exec_shortcut_is_blocked_off(run_cli, session):
    # exec/exec_exclusive bypass argparse, so the pre-parse gate must catch them.
    for cmd in ("exec", "exec_exclusive"):
        r = run_cli(cmd, "-s", session.session_id, "--", "true", env=_OFF)
        assert r.returncode == 2 and "turned OFF" in r.stderr, cmd


def test_help_is_blocked_off(run_cli):
    r = run_cli("help", env=_OFF)
    assert r.returncode == 2 and "turned OFF" in r.stderr


def test_allowlisted_commands_reachable_off(run_cli, session):
    # experiment json_test_schedule: allowlisted, needs only -C -> succeeds.
    r = run_cli("experiment", "-C", session.catalog_dir, "json_test_schedule",
                env=_OFF)
    assert r.returncode == 0, r.stderr
    # The other three allowlisted commands parse (reach their own arg handling)
    # rather than hitting the DRM gate.
    for name in ("new_catalog", "new_problem", "disable_problem"):
        r = run_cli(name, "-h", env=_OFF)
        assert r.returncode == 0, (name, r.stderr)


def test_help_hides_blocked_commands_off(run_cli):
    """--help lists only the allowlist (blocked tools are unregistered, not just
    refused), so the command menu does not advertise the full harness."""
    r = run_cli("--help", env=_OFF)
    assert r.returncode == 0
    for allowed in sorted(main._NO_HARNESS_ALLOWLIST):
        assert allowed in r.stdout, allowed
    for blocked in ("status", "build", "prompt", "new_root", "close_session"):
        assert blocked not in r.stdout, blocked


def test_unknown_command_still_argparse_error_off(run_cli):
    """A genuine typo (not a real command) falls through to argparse's usage
    against the allowed set, not the DRM message."""
    r = run_cli("staaatus", env=_OFF)
    assert r.returncode != 0
    assert "turned OFF" not in r.stderr
