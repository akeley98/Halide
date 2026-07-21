"""Machine directory, the flock lock hierarchy, and the session-handle store.

See idea.md ("The Machine Directory", "Locking", "Session Handles") and impl.md
("Tool Safety: Lock Hierarchy", "Session Handles on Disk").

Lock hierarchy, acquired strictly top-to-bottom (enforced here by a monotone
level counter):

  1. machine lock, shared      -- every tool, first thing in main()
  2. session lock, exclusive   -- tools touching the private workspace or
                                  mutating a session node (non-blocking:
                                  failure means concurrent session use)
  3. machine lock, exclusive   -- profiling / exec_exclusive: release the
                                  shared hold, re-acquire exclusively
  4. catalog lock, exclusive   -- tools accessing catalog state (blocking)

Not every tool takes every lock; a tool may skip levels but never go backwards.

Locks are held by keeping their file descriptors open for the life of the
process.  The OS releases them at process exit, strictly after the atexit
rollback handler in safety.py -- so rollback runs with the catalog lock still
held (see impl.md).  We therefore NEVER close a lock fd.

The lock files and the machine/handles directories are shared infrastructure:
they are created directly (not through safety.py), must survive rollback, and
are never deleted by the harness.
"""

import errno
import fcntl
import hashlib
import os

from .errors import DhHlError

# -- lock ordering levels ---------------------------------------------------
_L_NONE = 0
_L_MACHINE = 1
_L_SESSION = 2
_L_MACHINE_EXCL = 3
_L_CATALOG = 4

_state = {
    "level": _L_NONE,
    "machine_fd": None,
    "session_fd": None,
    "catalog_fd": None,
    "catalog_dir": None,      # abspath of the catalog whose lock we hold
    "machine_exclusive": False,
}


def _reset_for_tests():
    """Restore process-global lock state (test-only; see conftest)."""
    _state.update(level=_L_NONE, machine_fd=None, session_fd=None,
                  catalog_fd=None, catalog_dir=None, machine_exclusive=False)


def _fake_hold_for_tests(catalog_dir):
    """Test-only: set the lock state as if the machine + catalog(catalog_dir)
    locks are held, with NO real flock or filesystem access.  For test code that
    constructs a Catalog directly (outside the real acquire path) while still
    honoring the "a Catalog means its lock is held" invariant.  Monkeypatching
    aside, production code never calls this."""
    _state.update(level=_L_CATALOG, machine_fd=-1, session_fd=None,
                  catalog_fd=-1, catalog_dir=os.path.abspath(catalog_dir),
                  machine_exclusive=False)


# -- observability hook -----------------------------------------------------
# Normally None (a no-op).  A test may set locks._trace_sink to a list to record
# the sequence of lock acquisitions in order -- see the `lock_trace` fixture.
# This is the one observability concession in otherwise test-agnostic code.
_trace_sink = None


def _trace(event):
    if _trace_sink is not None:
        _trace_sink.append(event)


# -- machine directory ------------------------------------------------------
def machine_dir():
    base = os.environ.get("XDG_CACHE_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "dendritic_hl")


def handles_dir():
    return os.path.join(machine_dir(), "handles")


def _ensure_dir(path):
    # Infrastructure dir: created directly, NOT tracked by safety (must survive
    # rollback and be shared across processes).  exist_ok tolerates races.
    os.makedirs(path, exist_ok=True)


def _open_lock_file(path):
    _ensure_dir(os.path.dirname(path))
    # O_CREAT without O_EXCL: lock files are reused shared infrastructure.
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)


# -- lock acquisition -------------------------------------------------------
def acquire_machine_shared():
    """Acquire the machine lock in shared mode (blocking).  First lock taken by
    every tool; blocks only while another process holds it exclusively (i.e. is
    profiling / exec_exclusive)."""
    assert _state["level"] == _L_NONE, "machine lock acquired out of order"
    fd = _open_lock_file(os.path.join(machine_dir(), "machine.lock"))
    fcntl.flock(fd, fcntl.LOCK_SH)
    _state["machine_fd"] = fd
    _state["level"] = _L_MACHINE
    _trace(("machine", "shared"))


_SESSION_BUSY_MSG = """\
AGENTS: Concurrent usage of session detected.
Don't run concurrent tool invocations.
If the concurrent usage is not due to your error, stop and report the issue:
this could be due to a parent agent error (e.g. same session given to 2 agents)
or human user action interfering with agent work."""


def acquire_session(catalog_dir, session_id):
    """Acquire the per-session lock exclusively and non-blocking.  Failure to
    acquire is a hard error: it means another process holds this session (the
    Session Golden Rule was violated)."""
    assert _state["level"] == _L_MACHINE, "session lock acquired out of order"
    lock_path = os.path.join(catalog_dir, "private", session_id, "session.lock")
    fd = _open_lock_file(lock_path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise DhHlError(_SESSION_BUSY_MSG)
    _state["session_fd"] = fd
    _state["level"] = _L_SESSION
    _trace(("session", "exclusive"))


def upgrade_machine_exclusive():
    """Upgrade the machine lock from shared to exclusive by releasing then
    re-acquiring (per impl.md).  Not a deadlock risk: the session lock, if held,
    is ours and was taken non-blocking, and the catalog lock is not yet held."""
    assert _state["machine_fd"] is not None, "no machine lock to upgrade"
    assert _state["level"] in (_L_MACHINE, _L_SESSION), \
        "machine lock upgraded out of order"
    assert not _state["machine_exclusive"], "machine lock already exclusive"
    fd = _state["machine_fd"]
    fcntl.flock(fd, fcntl.LOCK_UN)
    fcntl.flock(fd, fcntl.LOCK_EX)
    _state["machine_exclusive"] = True
    _state["level"] = _L_MACHINE_EXCL
    _trace(("machine", "exclusive"))


def acquire_catalog(catalog_dir):
    """Acquire the per-catalog lock exclusively (blocking).  The load-bearing
    lock: it serializes all catalog mutation and is held through rollback."""
    assert _state["level"] in (_L_MACHINE, _L_SESSION, _L_MACHINE_EXCL), \
        "catalog lock acquired out of order"
    assert _state["catalog_fd"] is None, "catalog lock already held"
    lock_path = os.path.join(catalog_dir, "private", "catalog.lock")
    fd = _open_lock_file(lock_path)
    fcntl.flock(fd, fcntl.LOCK_EX)
    _state["catalog_fd"] = fd
    _state["catalog_dir"] = os.path.abspath(catalog_dir)
    _state["level"] = _L_CATALOG
    _trace(("catalog", "exclusive"))


def catalog_lock_held():
    """Whether this process holds a catalog lock."""
    return _state["catalog_fd"] is not None


def locked_catalog_dir():
    """The abspath of the catalog whose lock we hold, or None.  Lets a Catalog
    assert the lock is held *for it specifically*, not merely that some catalog
    lock is held."""
    return _state["catalog_dir"]


# -- session handle store ---------------------------------------------------
# Lock-free in both directions via the hard-link create-or-fail idiom: a handle
# file only ever becomes visible under its final name already holding complete
# content (staged in a sibling temp, then os.link'd into place).  See impl.md
# "Session Handles on Disk".

def _encode_pair(catalog_dir_abspath, session_full_id):
    # '\n' still round-trips if the path somehow contains one, and stays
    # readable versus '\0'.
    return (catalog_dir_abspath + "\n" + session_full_id + "\n").encode("utf-8")


def _read_bytes_or_none(path):
    """Raw bytes at *path*, or None for any unreadable/missing file.  Tolerant
    by design: a half-written temp or stray file is simply "not a match"."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def allocate_handle(catalog_dir_abspath, session_full_id):
    """Return the handle for this (catalog, session) pair, allocating it if
    needed.  Idempotent and safe under concurrency without any lock."""
    hd = handles_dir()
    _ensure_dir(hd)
    encoded = _encode_pair(catalog_dir_abspath, session_full_id)
    digest = hashlib.sha256(encoded).hexdigest()

    tmp = os.path.join(hd, ".alloc.{}.{}".format(os.getpid(),
                                                 os.urandom(6).hex()))
    with open(tmp, "wb") as f:
        f.write(encoded)  # fully written before it is ever linked
    try:
        for k in range(1, len(digest) + 1):
            cand = "tmp." + digest[:k]
            cand_path = os.path.join(hd, cand)
            try:
                os.link(tmp, cand_path)  # atomic create-or-fail
                return cand
            except FileExistsError:
                if _read_bytes_or_none(cand_path) == encoded:
                    return cand  # already ours; reuse
                continue  # collision with a different pair -> lengthen
        # Unreachable without a full-length sha256 collision.
        raise DhHlError("session handle allocation exhausted")
    finally:
        try:
            os.unlink(tmp)  # harmless to leak on crash; it is re-derivable
        except OSError:
            pass


def resolve_handle(handle):
    """Translate a session handle to (catalog_dir_abspath, session_full_id).
    No locking needed: any visible handle name points at complete bytes."""
    if not handle.startswith("tmp."):
        raise DhHlError("not a session handle (must start with 'tmp.'): "
                        + handle)
    data = _read_bytes_or_none(os.path.join(handles_dir(), handle))
    if data is None:
        raise DhHlError("unknown session handle: " + handle)
    parts = data.decode("utf-8", "replace").split("\n")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise DhHlError("corrupt session handle file: " + handle)
    return parts[0], parts[1]
