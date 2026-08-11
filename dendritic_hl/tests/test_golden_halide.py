"""Real-Halide, real-CLI (`run_cli`) coverage for the golden workflow (idea.md
"Golden Object Tools" / "Should-accept Schedule Tool"): new_golden's
algorithm-hlpipe gate, the `golden` magic [schedule ID], and should_accept's
golden check comparing REAL serialized algorithm pipelines (same algorithm ->
pass; changed algorithm -> fail), including `init_build --other golden`.

Uses tests/golden_gen.cpp, which emits the serialized pre-scheduling pipeline to
DENDRITIC_HL_ALGORITHM_HLPIPE.  Opt-in (marked halide): needs the local ~/Halide
build + ninja.  Everything goes through the real ./dh_hl subprocess.
"""

import json
import os
import shutil

import pytest

from dendritic_hl_lib import build
from conftest import _PKG_ROOT

pytestmark = [
    pytest.mark.halide,
    pytest.mark.skipif(not os.path.isdir(build.HALIDE_BUILD),
                       reason="no local Halide build at " + build.HALIDE_BUILD),
    pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not found"),
]

_GEN = os.path.join(_PKG_ROOT, "tests", "golden_gen.cpp")


def _line(out, prefix):
    for ln in out.splitlines():
        if ln.startswith(prefix):
            return ln[len(prefix):].strip()
    raise AssertionError("no line {!r} in:\n{}".format(prefix, out))


def _bootstrap(run_cli, tmp_path):
    cat_dir = str(tmp_path / "proj.dh_hl")
    (tmp_path / "p.txt").write_text("golden explore\n")
    r = run_cli("new_catalog", "-C", cat_dir, "seed", str(tmp_path / "p.txt"), _GEN)
    assert r.returncode == 0, r.stderr
    handle = _line(r.stdout, "Session handle: ")
    assert run_cli("init_workspace", "-s", handle).returncode == 0, "init_workspace"
    return cat_dir, handle


def _seed_id(run_cli, handle):
    # The seed idea's canonical == the default (status) schedule after
    # init_workspace, so the plain short-ID getter resolves it.
    return run_cli("schedule_short_id", "-s", handle).stdout.strip()


def _status_node(run_cli, handle):
    return _line(run_cli("status", "-s", handle).stdout, "Schedule node:")


def _set_params(run_cli, handle, params):
    r = run_cli("workspace_parameters", "-s", handle)
    assert r.returncode == 0, r.stderr
    with open(r.stdout.strip(), "w") as f:
        json.dump(params, f)


def _branch(run_cli, handle, name):
    """Branch a fresh (canonical-less) idea off the current canonical so a
    perturbed `init_build --target workspace` creates a new child node."""
    r = run_cli("new_idea", "-s", handle, name, "-", input=name + "\n")
    assert r.returncode == 0, r.stderr
    idea = _line(r.stdout, "Created idea ")
    assert run_cli("set_idea", "-s", handle, idea).returncode == 0, "set_idea"


def test_new_golden_hlpipe_gate_and_golden_magic(run_cli, tmp_path):
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    sid = _seed_id(run_cli, handle)
    (tmp_path / "rem.txt").write_text("golden remarks\n")

    # Before building, the algorithm-hlpipe gate refuses (real gate: the file
    # genuinely does not exist yet).
    r = run_cli("new_golden", "-s", handle, str(tmp_path / "rem.txt"), sid)
    assert r.returncode == 1 and "no algorithm hlpipe built" in r.stderr

    # Build the seed canonical -> the generator emits the algorithm hlpipe.
    assert run_cli("init_build", "-s", handle, sid, "--other", "none",
                   "--anchor", "none").returncode == 0, "init_build"
    assert run_cli("build", "-s", handle, "--only", "all").returncode == 0, "build"

    r = run_cli("new_golden", "-s", handle, str(tmp_path / "rem.txt"), sid)
    assert r.returncode == 0, r.stderr
    gid = r.stdout.strip()
    assert gid.startswith("golden_")

    # The `golden` magic [schedule ID] resolves to the golden schedule node.
    got = run_cli("schedule_full_id", "-C", cat_dir, "golden")
    want = run_cli("schedule_full_id", "-s", handle, sid)
    assert got.returncode == 0 and got.stdout.strip() == want.stdout.strip()

    # json_golden_info round-trips the schedule reference.
    r = run_cli("json_golden_info", "-C", cat_dir, gid)
    assert json.loads(r.stdout)["schedule"] == want.stdout.strip()


def test_golden_schedule_node_is_most_recent(run_cli, tmp_path):
    """The `golden` schedule node tracks the MOST RECENT golden object, across a
    later golden that clears it (schedule=none) and a later golden on a different
    node.  A bug returning the oldest golden -- or ignoring a later `none` --
    would be caught here (idea.md "Golden Object State")."""
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    sid1 = _seed_id(run_cli, handle)
    full1 = run_cli("schedule_full_id", "-s", handle, sid1).stdout.strip()
    (tmp_path / "r.txt").write_text("g1\n")

    # Build + golden on schedule 1.
    assert run_cli("init_build", "-s", handle, sid1, "--other", "none",
                   "--anchor", "none").returncode == 0, "init_build 1"
    assert run_cli("build", "-s", handle, "--only", "all").returncode == 0, "build 1"
    assert run_cli("new_golden", "-s", handle, str(tmp_path / "r.txt"),
                   sid1).returncode == 0, "new_golden 1"
    assert run_cli("schedule_full_id", "-C", cat_dir, "golden").stdout.strip() == full1

    # A later golden with NO schedule -> most recent wins -> `golden` now errors.
    assert run_cli("new_golden", "-s", handle, str(tmp_path / "r.txt"),
                   "none").returncode == 0, "new_golden none"
    r = run_cli("schedule_full_id", "-C", cat_dir, "golden")
    assert r.returncode == 1 and "no golden schedule node" in r.stderr

    # A second built schedule, then a golden on IT -> most recent is schedule 2.
    _branch(run_cli, handle, "second")
    _set_params(run_cli, handle, [{"add_const": 2}])
    assert run_cli("init_build", "-s", handle, "--target", "workspace",
                   "--other", "none", "--anchor", "none").returncode == 0, "init_build 2"
    assert run_cli("build", "-s", handle, "--only", "all").returncode == 0, "build 2"
    sid2 = _status_node(run_cli, handle)
    full2 = run_cli("schedule_full_id", "-s", handle, sid2).stdout.strip()
    assert full2 != full1
    assert run_cli("new_golden", "-s", handle, str(tmp_path / "r.txt"),
                   sid2).returncode == 0, "new_golden 2"
    # Most recent golden is schedule 2, NOT the older schedule 1.
    assert run_cli("schedule_full_id", "-C", cat_dir, "golden").stdout.strip() == full2


def test_should_accept_golden_match_then_algorithm_change(run_cli, tmp_path):
    cat_dir, handle = _bootstrap(run_cli, tmp_path)
    sid = _seed_id(run_cli, handle)
    (tmp_path / "rem.txt").write_text("baseline golden\n")

    # Golden = the seed canonical (add_const defaults to 1).  Build + profile so
    # the default problem is covered, then make it the golden.
    assert run_cli("init_build", "-s", handle, sid, "--other", "none",
                   "--anchor", "none").returncode == 0, "init_build seed"
    assert run_cli("build", "-s", handle, "--profile", "1").returncode == 0, "profile seed"
    assert run_cli("new_golden", "-s", handle, str(tmp_path / "rem.txt"),
                   sid).returncode == 0, "new_golden"

    # should_accept on the golden node itself: algorithm matches, problem
    # covered, golden unchanged since open -> all checks pass.
    r = run_cli("should_accept", "-s", handle, sid)
    assert r.returncode == 0 and "All checks passed" in r.stdout, r.stdout

    # Candidate with a CHANGED algorithm (add_const=2): build it against the
    # golden (`--other golden`, resolvable now) + profile, then should_accept
    # must report exactly the failed golden check.
    _branch(run_cli, handle, "changed")
    _set_params(run_cli, handle, [{"add_const": 2}])
    assert run_cli("init_build", "-s", handle, "--target", "workspace",
                   "--other", "golden", "--anchor", "none").returncode == 0, "init_build changed"
    assert run_cli("build", "-s", handle, "--profile", "1").returncode == 0, "profile changed"
    changed = _status_node(run_cli, handle)

    r = run_cli("should_accept", "-s", handle, changed)
    assert r.returncode == 0, r.stderr
    assert "failed golden check" in r.stdout, r.stdout
    assert "--allow-failed-golden" in r.stdout
    # The problem check passed (we profiled), so failed-problems is NOT reported.
    assert "failed problem check" not in r.stdout

    # close_session enforces it: refused without the override, accepted with it.
    (tmp_path / "c.txt").write_text("changed algorithm on purpose\n")
    assert run_cli("comment", "-s", handle, str(tmp_path / "c.txt"),
                   changed).returncode == 0, "comment"
    assert run_cli("canon", "-s", handle).returncode == 0, "canon"
    r = run_cli("close_session", "-s", handle, changed)
    assert r.returncode == 1 and "cannot close session" in r.stderr
    r = run_cli("close_session", "-s", handle, changed, "--allow-failed-golden")
    assert r.returncode == 0, r.stderr
    assert "Closed session" in r.stdout
