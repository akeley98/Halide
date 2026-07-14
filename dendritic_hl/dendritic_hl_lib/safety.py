"""Atomic-ish catalog mutation with best-effort rollback.

Tools create new files/directories through this module so that, if the tool
fails, an atexit handler can delete exactly what was created -- in reverse
order, so directories are empty when removed.

OVERRIDING SAFETY RULE (from idea.md): never use recursive directory delete.
We only ever os.remove() files we created and os.rmdir() directories we
created, in reverse creation order.  os.rmdir refuses non-empty directories,
which is a second line of defense against catastrophe.

Overwrites of the small set of allowed-to-change files (workspace C++,
current_idea_state.txt, result.txt, canonical.txt) are NOT rolled back; they
are deferred to commit() so they happen as late as possible.
"""

import atexit
import os
import signal

# LIFO list of ("file", path) / ("dir", path) we created this run.
_new_entries = []
# Deferred overwrites, applied (in order) at commit(): list of (path, bytes).
_pending_overwrites = []
_armed = False


def arm():
    """Install the rollback atexit handler and SIGQUIT->KeyboardInterrupt.

    Idempotent within a process; call once at tool startup.
    """
    global _armed
    if _armed:
        return
    _armed = True
    atexit.register(_rollback)
    try:
        signal.signal(signal.SIGQUIT, _on_sigquit)
    except (ValueError, OSError):
        # Not on the main thread, or platform without SIGQUIT; not fatal.
        pass


def _on_sigquit(signum, frame):
    raise KeyboardInterrupt


def new_dir(path):
    """Create *path* (a single directory level) if absent, recording it if we
    made it.  Returns True if we created it, False if it already existed."""
    try:
        os.mkdir(path)
    except FileExistsError:
        return False
    _new_entries.append(("dir", path))
    return True


def makedirs_tracked(path):
    """Create *path* and any missing ancestors, recording only the ones we
    actually create (shallow-to-deep, so reverse deletion empties them)."""
    path = os.path.abspath(path)
    missing = []
    cur = path
    while not os.path.isdir(cur):
        missing.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:  # reached filesystem root
            break
        cur = parent
    for d in reversed(missing):  # shallow -> deep
        new_dir(d)


def new_file(path, data):
    """Create a brand-new file at *path* with exclusive ("x") mode and record
    it for rollback.  *data* may be str (utf-8 encoded) or bytes.  The parent
    directory must already exist (use makedirs_tracked first)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    # Record before writing: if the "x" create succeeds but the write dies
    # partway, the file exists and must be rolled back.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    _new_entries.append(("file", path))
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    _maybe_inject_failure()


# -- test-only failure injection --------------------------------------------
# When DH_HL_TEST_FAIL_AFTER=<n> is set, raise after the n-th new_file this
# run.  Lets subprocess tests prove the atexit rollback restores a partial
# mutation.  The file(s) already created remain recorded, so rollback undoes
# them.  Guarded by the env var; a no-op in normal use.
_new_file_count = 0


def _maybe_inject_failure():
    global _new_file_count
    _new_file_count += 1
    n = os.environ.get("DH_HL_TEST_FAIL_AFTER")
    if n is not None and _new_file_count >= int(n):
        raise RuntimeError(
            "DH_HL_TEST_FAIL_AFTER: injected failure after {} new file(s)"
            .format(_new_file_count))


def queue_overwrite(path, data):
    """Defer an overwrite of an existing (or new) allowed-to-change file until
    commit().  Not rolled back on failure."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    _pending_overwrites.append((path, data))


def write_allowed(path, data):
    """Write one of the allowed-to-change files.  If it doesn't exist yet we
    create it with new_file (recorded, so rollback removes it and the dir it
    lives in can be rmdir'd).  If it exists we defer an overwrite that is NOT
    rolled back.  Existence is checked once; we assume no concurrent changes."""
    if os.path.exists(path):
        queue_overwrite(path, data)
    else:
        new_file(path, data)


def commit():
    """Finalize a successful tool run: apply deferred overwrites (as the very
    last mutating step), then disarm rollback so nothing gets deleted."""
    # Apply overwrites while rollback is still armed: if one throws, the tool
    # is considered failed and new files still roll back.
    for path, data in _pending_overwrites:
        with open(path, "wb") as f:
            f.write(data)
    _pending_overwrites.clear()
    # Disarm: clearing the record means the atexit handler is a no-op.
    _new_entries.clear()


def _rollback():
    """atexit handler: delete recorded new files/dirs in reverse order.

    Best-effort and interrupt-resistant.  An OSError on one entry is swallowed
    and we move on (no infinite retry).  A KeyboardInterrupt is swallowed and
    we retry the same entry so a stray Ctrl-C / SIGQUIT can't abort cleanup.
    """
    while _new_entries:
        kind, path = _new_entries[-1]
        try:
            if kind == "file":
                os.remove(path)
            else:
                os.rmdir(path)
        except OSError:
            # Give up on this one to avoid an infinite loop, keep going.
            _new_entries.pop()
            continue
        except KeyboardInterrupt:
            # Don't drop the entry; retry it.
            continue
        _new_entries.pop()
