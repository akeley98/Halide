"""Timestamps, hashes, and full ID handling for dendritic_hl.

Full IDs are the stable, on-disk identifiers.  Short IDs are the convenient
human/agent-facing form; they are *resolved* against a catalog (see catalog.py)
rather than parsed in isolation, because a short ID only has meaning relative to
the set of nodes that currently exist.

**`looks_like_*_id` naming (important).**  The predicates here are named
`looks_like_<kind>_id`, NOT `is_<kind>_id`, on purpose.  They test only whether a
string has the *syntactic shape* of that kind of ID -- length, separators,
character classes.  They do NOT prove the ID names a real object, and, crucially,
**the per-kind shapes are not disjoint**: a commentary local ID has the exact
shape of a schedule full ID; a benchmark local ID has the exact shape of a
benchmark set full ID; a problem full ID is just a 64-char hash.  So a true
`looks_like_schedule_id(x)` does not mean `x` is a schedule, and you cannot
dispatch on "which kind is this ID?" by trying these in turn -- more than one can
match the same string.  Kind is determined by *where* an ID is used/stored, not by
its shape.  The `looks_like_` name is the standing reminder of both caveats.

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


_TIMESTAMP_FORMAT = "%Y-%m-%dT%H%M%S_%fZ"


def now_timestamp():
    """Current UTC wall-clock time as a dendritic_hl timestamp string."""
    return datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def parse_timestamp(s):
    """Parse a dendritic_hl timestamp string into a UTC-aware datetime."""
    return datetime.strptime(s, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)


def sha256_hex(data):
    """sha256 of *data* (bytes) as lowercase hex."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def schedule_content_hash(source, params_text):
    """The content hash forming part of a schedule node's ID: sha256 over the
    UTF-8 encoding of generator.cpp concatenated with generator_parameters.json
    (idea.md "Hash Format" / impl.md "Schedule Nodes on Disk").

    Both arguments may be str or bytes; str is encoded as UTF-8.  This is the
    single definition of the hash, shared by node creation and the workspace's
    matching-node lookup, so the two can never disagree."""
    def to_bytes(x):
        return x.encode("utf-8") if isinstance(x, str) else x
    return sha256_hex(to_bytes(source) + to_bytes(params_text))


def is_timestamp(s):
    return bool(_TIMESTAMP_RE.match(s))


def is_hash(s):
    return bool(_HASH_RE.match(s))


def is_proposal_name(s):
    return bool(_PROPOSAL_NAME_RE.match(s))


# ---- Schedule node full IDs ------------------------------------------------

def make_schedule_id(timestamp, source_hash):
    return "{}_{}".format(timestamp, source_hash)


def looks_like_schedule_id(full_id):
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


def looks_like_idea_id(full_id):
    # Idea ID = proposal_name + "_" + schedule_id(90).  Split off the fixed-width
    # tail; the char just before it must be the joining underscore.
    if len(full_id) < 1 + 1 + SCHEDULE_ID_LEN:
        return False
    parent = full_id[-SCHEDULE_ID_LEN:]
    sep = full_id[-SCHEDULE_ID_LEN - 1]
    proposal = full_id[:-SCHEDULE_ID_LEN - 1]
    return sep == "_" and looks_like_schedule_id(parent) and is_proposal_name(proposal)


def idea_proposal_name(full_id):
    return full_id[:-SCHEDULE_ID_LEN - 1]


def idea_parent_id(full_id):
    return full_id[-SCHEDULE_ID_LEN:]


# ---- WarningToggle sub-object full IDs --------------------------------------
#
# WarningToggle full ID = "{parent schedule full ID}_{timestamp}" (idea.md
# "WarningToggle State").  The "{timestamp}" tail is the sub-object's *local ID*.
# Both halves are fixed width (90 + 1 + 25 = 116), so parsing is unambiguous.

WARNING_TOGGLE_ID_LEN = SCHEDULE_ID_LEN + 1 + TIMESTAMP_LEN  # 116


def make_warning_toggle_id(schedule_id, timestamp):
    return "{}_{}".format(schedule_id, timestamp)


def looks_like_warning_toggle_id(full_id):
    if len(full_id) != WARNING_TOGGLE_ID_LEN:
        return False
    sched = full_id[:SCHEDULE_ID_LEN]
    sep = full_id[SCHEDULE_ID_LEN]
    ts = full_id[SCHEDULE_ID_LEN + 1:]
    return sep == "_" and looks_like_schedule_id(sched) and is_timestamp(ts)


def warning_toggle_schedule_id(full_id):
    return full_id[:SCHEDULE_ID_LEN]


def warning_toggle_timestamp(full_id):
    return full_id[SCHEDULE_ID_LEN + 1:]


# ---- Benchmark sub-object full IDs ------------------------------------------
#
# Benchmark full ID = "{parent schedule full ID}_{hostname}_{timestamp}" (idea.md
# "Schedule Node State").  The "{hostname}_{timestamp}" tail is the sub-object's
# *local ID* -- exactly the "{hostname}_{ts}" stem of its bench/ file name.  The
# hostname is the *sanitized* stable hostname ([A-Za-z0-9_-]+, may contain '_'),
# but the timestamp is fixed width and the schedule prefix is fixed width, so the
# hostname in the middle is whatever remains.

def make_benchmark_id(schedule_id, hostname, timestamp):
    return "{}_{}_{}".format(schedule_id, hostname, timestamp)


def benchmark_local_id(hostname, timestamp):
    return "{}_{}".format(hostname, timestamp)


def looks_like_benchmark_local_id(local):
    # "{hostname}_{timestamp}": strip the fixed-width timestamp tail; the char
    # before it must be '_', and the remaining hostname must be nonempty and
    # match the sanitized alphabet.
    if len(local) < 1 + 1 + TIMESTAMP_LEN:
        return False
    ts = local[-TIMESTAMP_LEN:]
    sep = local[-TIMESTAMP_LEN - 1]
    host = local[:-TIMESTAMP_LEN - 1]
    return (sep == "_" and is_timestamp(ts) and bool(host)
            and re.match(r"[A-Za-z0-9_-]+\Z", host) is not None)


def looks_like_benchmark_id(full_id):
    if len(full_id) < SCHEDULE_ID_LEN + 1:
        return False
    sched = full_id[:SCHEDULE_ID_LEN]
    sep = full_id[SCHEDULE_ID_LEN]
    local = full_id[SCHEDULE_ID_LEN + 1:]
    return sep == "_" and looks_like_schedule_id(sched) and looks_like_benchmark_local_id(local)


def benchmark_schedule_id(full_id):
    return full_id[:SCHEDULE_ID_LEN]


def benchmark_local_part(full_id):
    return full_id[SCHEDULE_ID_LEN + 1:]


def benchmark_hostname(local):
    return local[:-TIMESTAMP_LEN - 1]


def benchmark_timestamp(local):
    return local[-TIMESTAMP_LEN:]


# ---- Benchmark set full IDs -------------------------------------------------
#
# Benchmark set full ID = "{sanitized hostname}_{timestamp}" (idea.md "Benchmark
# Set State").  There is no schedule prefix (a set spans schedule nodes) and no
# short-ID form.  The hostname uniquifies across machines; the minted timestamp
# uniquifies on one machine.  Same shape as a benchmark *local* ID, but the two
# resolve in different namespaces (sets are top-level, matched against
# benchmark_sets/ file names), so there is no ambiguity.

# ---- Problem object full IDs / short names ---------------------------------
#
# Problem full ID = sha256 hex of the canonical argv.json text (idea.md "Problem
# Object State" / impl.md "Problem Objects on Disk").  So a problem is identified
# purely by its command line; two problems with identical argv are the same
# object.  There is no timestamp; uniqueness is content-addressed.  The short
# name is a mutable, not-necessarily-unique label used only in the short ID
# "problem.{short name}".

_PROBLEM_SHORT_NAME_RE = re.compile(r"[A-Za-z0-9_]+\Z")


def is_problem_short_name(s):
    return bool(_PROBLEM_SHORT_NAME_RE.match(s))


def looks_like_problem_id(full_id):
    """A problem full ID is exactly the 64-char lowercase-hex content hash."""
    return is_hash(full_id)


def make_benchmark_set_id(hostname, timestamp):
    return "{}_{}".format(hostname, timestamp)


def looks_like_benchmark_set_id(full_id):
    # "{hostname}_{timestamp}": strip the fixed-width timestamp tail; the char
    # before it must be '_', and the hostname must match the sanitized alphabet.
    if len(full_id) < 1 + 1 + TIMESTAMP_LEN:
        return False
    ts = full_id[-TIMESTAMP_LEN:]
    sep = full_id[-TIMESTAMP_LEN - 1]
    host = full_id[:-TIMESTAMP_LEN - 1]
    return (sep == "_" and is_timestamp(ts) and bool(host)
            and re.match(r"[A-Za-z0-9_-]+\Z", host) is not None)


# ---- Golden object full IDs ------------------------------------------------
#
# Golden full ID = "golden_{timestamp}".  The "golden_" prefix keeps it clear of
# schedule full IDs (fixed-width {timestamp}_{hash}) and every short ID (which
# always contains a '.'), so a golden object ID can double as a magic
# [schedule ID] value without collision (idea.md "Golden Object State").

GOLDEN_ID_PREFIX = "golden_"


def make_golden_id(timestamp):
    return GOLDEN_ID_PREFIX + timestamp


def looks_like_golden_id(full_id):
    return (full_id.startswith(GOLDEN_ID_PREFIX)
            and is_timestamp(full_id[len(GOLDEN_ID_PREFIX):]))


def golden_timestamp(full_id):
    return full_id[len(GOLDEN_ID_PREFIX):]


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


def looks_like_session_id(full_id):
    return bool(_SESSION_ID_RE.match(full_id))


def session_depth(full_id):
    # All digits before the first '_' (formatted %d, so no leading zeros).
    return int(full_id.split("_", 1)[0])


def session_timestamp(full_id):
    # The fixed-width timestamp immediately following "{depth}_".
    rest = full_id.split("_", 1)[1]
    return rest[:TIMESTAMP_LEN]
