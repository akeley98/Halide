"""Phase 1: machine directory, session-handle store, and exec/exec_exclusive.

The handle store is exercised in-process (it is lock-free and pure).  exec /
exec_exclusive are exercised as real subprocesses, including a timing test that
two exec_exclusive runs do not overlap (the exclusive machine lock serializes
them).

FUTURE: the timing test is inherently wall-clock based and therefore tolerant
rather than exact; a scheduler hiccup could in principle perturb it.  It is
kept because it is the most direct evidence the exclusive lock serializes, but
it is not a microsecond-precise guarantee.
"""

import subprocess
import sys
import time

import pytest

from conftest import DH_HL
from dendritic_hl_lib import locks
from dendritic_hl_lib.errors import DhHlError


# -- machine directory ------------------------------------------------------
def test_machine_dir_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert locks.machine_dir() == str(tmp_path / "dendritic_hl")
    assert locks.handles_dir() == str(tmp_path / "dendritic_hl" / "handles")


# -- session handle store ---------------------------------------------------
def test_handle_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cat = "/some/catalog.dh_hl"
    sid = "0_2026-07-17T000000_000000Z_user@host"
    h = locks.allocate_handle(cat, sid)
    assert h.startswith("tmp.")
    assert locks.resolve_handle(h) == (cat, sid)


def test_handle_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cat, sid = "/c.dh_hl", "0_2026-07-17T000000_000000Z_a@b"
    assert locks.allocate_handle(cat, sid) == locks.allocate_handle(cat, sid)


def test_handle_distinct_pairs_resolve_independently(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    pairs = [("/c1.dh_hl", "0_2026-07-17T000000_000000Z_a@b"),
             ("/c2.dh_hl", "0_2026-07-17T000000_000000Z_a@b"),
             ("/c1.dh_hl", "1_2026-07-17T000000_000001Z_a@b")]
    handles = {locks.allocate_handle(c, s): (c, s) for c, s in pairs}
    assert len(handles) == 3  # three distinct handles
    for h, pair in handles.items():
        assert locks.resolve_handle(h) == pair


def test_resolve_unknown_handle_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    with pytest.raises(DhHlError):
        locks.resolve_handle("tmp.deadbeef")


def test_resolve_non_handle_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    with pytest.raises(DhHlError):
        locks.resolve_handle("0_2026-07-17T000000_000000Z_a@b")


def test_junk_in_handles_dir_is_tolerated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cat, sid = "/c.dh_hl", "0_2026-07-17T000000_000000Z_a@b"
    h = locks.allocate_handle(cat, sid)
    # Drop a stray/garbage file alongside; a good handle still resolves.
    import os
    with open(os.path.join(locks.handles_dir(), ".alloc.stale"), "wb") as f:
        f.write(b"\x00\x01garbage")
    assert locks.resolve_handle(h) == (cat, sid)


# -- exec / exec_exclusive --------------------------------------------------
def _run(*args, env=None):
    import os
    e = dict(os.environ)
    e["XDG_CACHE_HOME"] = env
    return subprocess.run([DH_HL, *args], capture_output=True, text=True, env=e)


def test_exec_propagates_exit_code(tmp_path):
    xdg = str(tmp_path)
    r = _run("exec", "--", sys.executable, "-c", "import sys; sys.exit(7)", env=xdg)
    assert r.returncode == 7


def test_exec_runs_command(tmp_path):
    r = _run("exec", "--", sys.executable, "-c", "print('hello')", env=str(tmp_path))
    assert r.returncode == 0
    assert "hello" in r.stdout


def test_exec_requires_double_dash(tmp_path):
    r = _run("exec", env=str(tmp_path))
    assert r.returncode == 1
    assert "--" in r.stderr


def test_exec_exclusive_propagates_exit_code(tmp_path):
    r = _run("exec_exclusive", "--", sys.executable, "-c",
             "import sys; sys.exit(3)", env=str(tmp_path))
    assert r.returncode == 3


def test_exec_exclusive_runs_serialize(tmp_path):
    """Two concurrent exec_exclusive runs sharing one machine lock must not
    overlap in time."""
    xdg = str(tmp_path)
    import os
    e = dict(os.environ)
    e["XDG_CACHE_HOME"] = xdg

    script = ("import sys, time\n"
              "p = sys.argv[1]\n"
              "start = time.time()\n"
              "time.sleep(0.3)\n"
              "end = time.time()\n"
              "open(p, 'w').write('{} {}'.format(start, end))\n")
    out_a = str(tmp_path / "a.txt")
    out_b = str(tmp_path / "b.txt")

    def spawn(out):
        return subprocess.Popen(
            [DH_HL, "exec_exclusive", "--", sys.executable, "-c", script, out],
            env=e)

    pa = spawn(out_a)
    pb = spawn(out_b)
    assert pa.wait() == 0
    assert pb.wait() == 0

    a0, a1 = (float(x) for x in open(out_a).read().split())
    b0, b1 = (float(x) for x in open(out_b).read().split())
    # Disjoint intervals (with a small tolerance): one finished before the
    # other started.  Overlap would mean the exclusive lock failed to serialize.
    disjoint = a1 <= b0 + 0.05 or b1 <= a0 + 0.05
    assert disjoint, "exec_exclusive runs overlapped: {} {}".format((a0, a1), (b0, b1))
