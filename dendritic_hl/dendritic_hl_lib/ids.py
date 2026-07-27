"""Timestamps, hashes, and full ID handling for dendritic_hl.

Full IDs are the stable, on-disk identifiers.  Short IDs are the convenient
human/agent-facing form; they are *resolved* against a catalog (see catalog.py)
rather than parsed in isolation, because a short ID only has meaning relative to
the set of nodes that currently exist.

Formats (see idea.md):

* Timestamp: strftime("%Y-%m-%dT%H%M%S_%fZ") in UTC, e.g. 2026-07-14T153045_123456Z
  (25 chars, lexicographically sortable for years 1000..9999).
* Hash: sha256 of the UTF-8 source bytes, lowercase hex (64 chars).
* Schedule node full ID: "{timestamp}_{hash}" -> exactly 90 chars.
* Idea node full ID: "{proposal_name}_{parent_schedule_full_id}".
  proposal_name is 1..72 chars of [A-Za-z0-9_].  Because the schedule full ID
  is fixed width (90), the split point is unambiguous.
"""

import hashlib
import re
import subprocess
import sys
from datetime import datetime, timezone

TIMESTAMP_LEN = 25
HASH_LEN = 64
SCHEDULE_ID_LEN = 90  # TIMESTAMP_LEN + len("_") + HASH_LEN

# A timestamp with the exact fixed-width shape produced by now_timestamp().
_TIMESTAMP_RE = re.compile(r"\d{4}-\d\d-\d\dT\d{6}_\d{6}Z\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_PROPOSAL_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,72}\Z")


def now_timestamp():
    """Current UTC wall-clock time as a dendritic_hl timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S_%fZ")


def sha256_hex(data):
    """sha256 of *data* (bytes) as lowercase hex."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def is_timestamp(s):
    return bool(_TIMESTAMP_RE.match(s))


def is_hash(s):
    return bool(_HASH_RE.match(s))


def is_proposal_name(s):
    return bool(_PROPOSAL_NAME_RE.match(s))


# ---- Schedule node full IDs ------------------------------------------------

def make_schedule_id(timestamp, source_hash):
    return "{}_{}".format(timestamp, source_hash)


def is_schedule_id(full_id):
    if len(full_id) != SCHEDULE_ID_LEN:
        return False
    ts, sep, h = full_id[:TIMESTAMP_LEN], full_id[TIMESTAMP_LEN], full_id[TIMESTAMP_LEN + 1:]
    return sep == "_" and is_timestamp(ts) and is_hash(h)


def schedule_timestamp(full_id):
    return full_id[:TIMESTAMP_LEN]


def schedule_hash(full_id):
    return full_id[TIMESTAMP_LEN + 1:]


# ---- Idea node full IDs ----------------------------------------------------

def make_idea_id(proposal_name, parent_schedule_id):
    return "{}_{}".format(proposal_name, parent_schedule_id)


def is_idea_id(full_id):
    # Idea ID = proposal_name + "_" + schedule_id(90).  Split off the fixed-width
    # tail; the char just before it must be the joining underscore.
    if len(full_id) < 1 + 1 + SCHEDULE_ID_LEN:
        return False
    parent = full_id[-SCHEDULE_ID_LEN:]
    sep = full_id[-SCHEDULE_ID_LEN - 1]
    proposal = full_id[:-SCHEDULE_ID_LEN - 1]
    return sep == "_" and is_schedule_id(parent) and is_proposal_name(proposal)


def idea_proposal_name(full_id):
    return full_id[:-SCHEDULE_ID_LEN - 1]


def idea_parent_id(full_id):
    return full_id[-SCHEDULE_ID_LEN:]


# ---- Session node full IDs -------------------------------------------------
#
# Session full ID = "{depth}_{timestamp}_{username}@{hostname}" (see idea.md).
# depth is formatted %d (no leading zeros).  username/hostname are sanitized to
# [A-Za-z0-9_-] so they never contain '_' ambiguity with the separators... they
# *can* contain '_', but the timestamp is fixed width and the '@' is unique, so
# parsing stays unambiguous: split off depth at the first '_', take the next
# fixed-width timestamp, then the remainder is "user@host".

_SESSION_ID_RE = re.compile(
    r"(?:0|[1-9]\d*)_"
    r"\d{4}-\d\d-\d\dT\d{6}_\d{6}Z_"
    r"[A-Za-z0-9_-]+@[A-Za-z0-9_-]+\Z")


def stable_hostname():
    """A human-readable hostname that stays stable across reboots/networks.

    Deliberately de-anonymizing (see idea.md).  Platform-specific because the
    plain `socket.gethostname()` is not stable enough:

    * macOS: ``scutil --get ComputerName`` (the only stable option here, so we
      tolerate that "hostname" is a misnomer -- e.g. "David's MacBook Pro").
    * Linux: the contents of ``/etc/hostname``.

    Falls back to ``socket.gethostname()`` if the platform path fails, so a
    profiling run never crashes just because the name lookup misbehaved.  The
    RAW string is returned; callers that use it in an ID or filename must run it
    through ``sanitize_component`` first (only the benchmark JSON ``hostname``
    field keeps the raw value, as a hedge against losing information)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["scutil", "--get", "ComputerName"],
                                 capture_output=True, text=True, check=True)
            name = out.stdout.strip()
            if name:
                return name
        else:
            # Best-effort Linux path (David tests this on the mantissa machine).
            with open("/etc/hostname", "r", encoding="utf-8") as f:
                name = f.read().strip()
            if name:
                return name
    except Exception:
        pass
    import socket
    return socket.gethostname()


def sanitize_component(s, maxlen=64):
    """Sanitize a username/hostname for use in a session ID: map anything
    outside [A-Za-z0-9_-] to '_', cap the length, never return empty."""
    s = re.sub(r"[^A-Za-z0-9_-]", "_", s)[:maxlen]
    return s or "_"


def make_session_id(depth, timestamp, username, hostname):
    return "{:d}_{}_{}@{}".format(int(depth), timestamp,
                                  sanitize_component(username),
                                  sanitize_component(hostname))


def is_session_id(full_id):
    return bool(_SESSION_ID_RE.match(full_id))


def session_depth(full_id):
    # All digits before the first '_' (formatted %d, so no leading zeros).
    return int(full_id.split("_", 1)[0])


def session_timestamp(full_id):
    # The fixed-width timestamp immediately following "{depth}_".
    rest = full_id.split("_", 1)[1]
    return rest[:TIMESTAMP_LEN]
