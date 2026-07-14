"""Timestamps, hashes, and full/short ID handling for dendritic_hl.

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
