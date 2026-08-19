"""Real-Halide, real-CLI coverage for `build --gen-timeout` / `--exec-timeout`.

These are the ONLY two build steps dh_hl can reliably time-limit: the generator
emit and the pipeline run, both leaf child processes killed via SIGTERM -> grace
-> SIGKILL on the direct child (never a process group; ninja/C++ is intentionally
not timed).  A timeout can't be exercised by monkeypatching -- the whole point is
real signal delivery to a real child -- so everything here goes through the actual
`./dh_hl` subprocess against a real Halide toolchain, using tests/timeout_gen.cpp
(env-gated hang for --gen-timeout; a slow-to-run pipeline for --exec-timeout).

Opt-in (marked `halide`): needs the local ~/Halide build + ninja.
"""

import os
import shutil

import pytest

from dendritic_hl_lib.build import _KILL_GRACE_SEC
from conftest import _PKG_ROOT, HALIDE_DIR, HALIDE_BUILD_DIR, add_default_problem_cli

pytestmark = [
    pytest.mark.halide,
    pytest.mark.skipif(not os.path.isdir(HALIDE_BUILD_DIR),
                       reason="no local Halide build at " + HALIDE_BUILD_DIR),
    pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not found"),
]

_TIMEOUT_GEN = os.path.join(_PKG_ROOT, "tests", "timeout_gen.cpp")

# The generator busy-waits this long (ms) when asked to hang: far longer than any
# timeout below, so a correctly-killed run dies well before this, and a BROKEN
# timeout still self-terminates here (loud test failure) rather than hanging CI.
_HANG_MS = "60000"


def _line(out, prefix):
    for ln in out.splitlines():
        if ln.startswith(prefix):
            return ln[len(prefix):].strip()
    raise AssertionError("no line starting {!r} in:\n{}".format(prefix, out))


def _bootstrap(run_cli, tmp_path):
    """new_catalog + init_workspace + set_halide_path via the CLI, pointed at the
    timeout_gen.cpp fixture.  Returns (cat_dir, handle)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "p.txt").write_text("explore timeout\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed",
                str(tmp_path / "p.txt"), _TIMEOUT_GEN)
    assert r.returncode == 0, r.stderr
    add_default_problem_cli(run_cli, cat_dir)
    handle = _line(r.stdout, "Session handle: ")
    assert run_cli("init_workspace", "-s", handle).returncode == 0
    assert run_cli("set_halide_path", "-s", handle, HALIDE_DIR).returncode == 0
    return cat_dir, handle


def _branch_fresh_idea(run_cli, handle):
    """Branch a fresh (canonical-less) idea so `init_build --target workspace`
    can create a child node (mirrors test_build_cli_halide.py)."""
    r = run_cli("new_idea", "-s", handle, "explore", "-",
                input="explore variation\n")
    assert r.returncode == 0, r.stderr
    idea = _line(r.stdout, "Created idea ")
    assert run_cli("set_idea", "-s", handle, idea).returncode == 0


def _set_params(run_cli, handle, params):
    import json
    r = run_cli("workspace_parameters", "-s", handle)
    assert r.returncode == 0, r.stderr
    with open(r.stdout.strip(), "w") as f:
        json.dump(params, f)


def _init_target_only(run_cli, handle):
    r = run_cli("init_build", "-s", handle, "--target", "workspace",
                "--other", "none", "--anchor", "none")
    assert r.returncode == 0, r.stderr


def _last_heartbeat(path):
    """The last elapsed-seconds value the hanging generator wrote (== how long it
    lived before being killed).  A SIGKILL mid-write can leave a partial final
    line, so ignore unparseable lines and take the last good one."""
    vals = []
    for ln in path.read_text().splitlines():
        try:
            vals.append(float(ln.strip()))
        except ValueError:
            pass
    assert vals, "generator wrote no heartbeat (did it reach generate()?)"
    return vals[-1]


# ---------------------------------------------------------------------------

def test_gen_timeout_terminates_on_sigterm(run_cli, tmp_path):
    """A generator that hangs during emit is killed by --gen-timeout: it honors
    SIGTERM, so it dies at ~the timeout, the build fails, and no SIGKILL is
    needed."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _branch_fresh_idea(run_cli, handle)
    _set_params(run_cli, handle, [{}])  # runtime_work defaults to 0
    _init_target_only(run_cli, handle)

    hb = tmp_path / "heartbeat.txt"
    r = run_cli("build", "-s", handle, "--gen-timeout", "1",
                env={"DH_HL_TEST_HANG_MS": _HANG_MS,
                     "DH_HL_TEST_HEARTBEAT": str(hb)})
    assert r.returncode != 0, r.stdout + r.stderr
    assert "TIMEOUT" in r.stderr and "SIGTERM" in r.stderr, r.stderr
    assert "SIGKILL" not in r.stderr, r.stderr
    # It actually hung (>~0.5s) but was killed near the 1s deadline, nowhere near
    # the 60s it would otherwise run.
    last = _last_heartbeat(hb)
    assert 0.5 < last < 1.0 + _KILL_GRACE_SEC, last


def test_gen_timeout_escalates_to_sigkill(run_cli, tmp_path):
    """A generator that IGNORES SIGTERM survives past the timeout and is killed by
    the SIGKILL backstop ~grace seconds later."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _branch_fresh_idea(run_cli, handle)
    _set_params(run_cli, handle, [{}])
    _init_target_only(run_cli, handle)

    hb = tmp_path / "heartbeat.txt"
    r = run_cli("build", "-s", handle, "--gen-timeout", "1",
                env={"DH_HL_TEST_HANG_MS": _HANG_MS,
                     "DH_HL_TEST_IGNORE_SIGTERM": "1",
                     "DH_HL_TEST_HEARTBEAT": str(hb)})
    assert r.returncode != 0, r.stdout + r.stderr
    assert "SIGKILL" in r.stderr, r.stderr
    # Survived the SIGTERM deadline (>1s) and was SIGKILLed at ~1s + grace, well
    # before the 60s hang budget.
    last = _last_heartbeat(hb)
    assert 1.0 < last < 1.0 + _KILL_GRACE_SEC + 3.0, last


def test_exec_timeout_kills_slow_pipeline(run_cli, tmp_path):
    """A pipeline that compiles fast but runs slowly is killed by --exec-timeout:
    the emit/compile succeed, the profiler run times out, the build fails, and no
    benchmark set is produced."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    _branch_fresh_idea(run_cli, handle)
    _set_params(run_cli, handle, [{"runtime_work": 200000}])
    _init_target_only(run_cli, handle)

    r = run_cli("build", "-s", handle, "--profile", "1", "--exec-timeout", "2")
    assert r.returncode != 0, r.stdout + r.stderr
    # The compile/emit got through (so the timeout was on the RUN, not the build).
    assert "end Halide generator 0 success" in r.stdout, r.stdout
    # The pipeline run was the thing timed out, and nothing was recorded.
    assert "TIMEOUT" in r.stderr and "pipeline run" in r.stderr, r.stderr
    assert "Benchmark set ID:" not in r.stdout, r.stdout
