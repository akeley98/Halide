"""In-memory model of a dendritic_hl catalog, with lazy load + dirty flush.

Design (see impl.md "Tool Internal Design"):

TODO update for session changes.

* A Catalog owns one CurrentIdeaState and two dicts (schedules, ideas) keyed by
  full ID.  The dicts are populated by listing sch/ and idea/ once; each entry
  starts as an empty-state node that lazily reads its files on demand.
* Each on-disk file is owned by exactly one in-memory object.  Objects are
  dirtied by their own setters (or on non-disk creation) and register with the
  Catalog's dirty set; nothing dirties anything recursively.
* Catalog.flush() calls each dirty object's single flush().  The physical
  ordering "all new files created before any existing file is overwritten" is
  NOT provided by flush() -- it is guaranteed by the safety module: new_file
  writes immediately, while write_allowed/queue_overwrite defer the overwrite to
  safety.commit(), which Context.finish() runs strictly after the whole flush
  loop.  So flush() may create + queue in any object order safely (each on-disk
  file is owned by exactly one object, so there is never a cross-object race).
"""

import getpass
import json
import os
import socket

from . import ids
from . import locks
from . import safety
from .errors import DhHlError, HarnessError

# Sentinel distinguishing "not yet looked at disk" from a real loaded value.
# Several fields are lazily populated and hold one of three kinds of value:
#
#   _UNLOADED  -- unknown: we have NOT yet read (or stat'd) the backing file.
#   None       -- known absence: we looked, and the state genuinely isn't there
#                 (e.g. no parent.txt => a root node; no canonical.txt => no
#                 canonical schedule).  This is a loaded value, not "unset".
#   <value>    -- known presence: the loaded contents.
#
# The point of the sentinel is precisely so `None` can mean the second case
# without being confused with the first -- so we never re-stat a file we've
# already found to be absent (part of the "don't read any file twice" goal).
_UNLOADED = object()


# ---------------------------------------------------------------------------
# Sub-objects
# ---------------------------------------------------------------------------

class Commentary:
    """One commentary file: comment/{ts}.txt or comment/{ts}_{importance}.txt."""

    def __init__(self, schedule, timestamp, importance, text=_UNLOADED, is_new=False):
        self.schedule = schedule
        self.timestamp = timestamp
        self.importance = importance  # int or None
        self._text = text
        if is_new:
            self.schedule.catalog._mark_dirty(self)

    @property
    def filename(self):
        if self.importance is None:
            return "{}.txt".format(self.timestamp)
        return "{}_{:d}.txt".format(self.timestamp, self.importance)

    @property
    def path(self):
        return os.path.join(self.schedule.comment_dir, self.filename)

    @property
    def text(self):
        if self._text is _UNLOADED:
            with open(self.path, "r", encoding="utf-8") as f:
                self._text = f.read()
        return self._text

    def flush(self):
        safety.makedirs_tracked(self.schedule.comment_dir)
        safety.new_file(self.path, self.text)


class Benchmark:
    """One benchmark file: bench/{hostname}_{ts}.json holding a benchmark JSON
    object (see idea.md "Benchmark JSON Format")."""

    def __init__(self, schedule, filename, data=_UNLOADED, is_new=False):
        self.schedule = schedule
        self.filename = filename
        self._data = data
        if is_new:
            self.schedule.catalog._mark_dirty(self)

    @property
    def path(self):
        return os.path.join(self.schedule.bench_dir, self.filename)

    @property
    def data(self):
        if self._data is _UNLOADED:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        return self._data

    def flush(self):
        safety.makedirs_tracked(self.schedule.bench_dir)
        safety.new_file(self.path, json.dumps(self.data, indent=1) + "\n")


# ---------------------------------------------------------------------------
# Schedule node
# ---------------------------------------------------------------------------

class ScheduleNode:
    def __init__(self, catalog, full_id, is_new=False, source=None):
        self.catalog = catalog
        self.full_id = full_id
        self.is_new = is_new
        self._source = source if source is not None else _UNLOADED
        self._parent_id = _UNLOADED       # _UNLOADED / None (=root) / parent id str
        self._result = _UNLOADED          # _UNLOADED / result str (absent file => "c++ error")
        self._result_dirty = False
        self._commentary = _UNLOADED      # list[Commentary]
        self._benchmarks = _UNLOADED      # list[Benchmark]
        # Derived child edges (filled by Catalog._ensure_linked):
        self.child_idea_ids = None        # list[str] or None if not linked
        if is_new:
            self.catalog._mark_dirty(self)

    # -- identity --------------------------------------------------------
    @property
    def timestamp(self):
        return ids.schedule_timestamp(self.full_id)

    @property
    def hash(self):
        return ids.schedule_hash(self.full_id)

    # -- paths -----------------------------------------------------------
    @property
    def dir(self):
        return os.path.join(self.catalog.sch_dir, self.full_id)

    @property
    def comment_dir(self):
        return os.path.join(self.dir, "comment")

    @property
    def bench_dir(self):
        return os.path.join(self.dir, "bench")

    # -- source ----------------------------------------------------------
    @property
    def source(self):
        if self._source is _UNLOADED:
            with open(os.path.join(self.dir, "generator.cpp"), "rb") as f:
                self._source = f.read().decode("utf-8")
        return self._source

    # -- parent ----------------------------------------------------------
    @property
    def parent_id(self):
        # Tri-state via _UNLOADED: absence of parent.txt loads as None, which
        # is the meaningful "this is a root node" value (not "unknown").
        if self._parent_id is _UNLOADED:
            p = os.path.join(self.dir, "parent.txt")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    self._parent_id = f.read().strip()
            else:
                self._parent_id = None
        return self._parent_id

    def is_root(self):
        return self.parent_id is None

    def parent_idea(self):
        if self.is_root():
            return None
        return self.catalog.get_idea(self.parent_id)

    def is_major(self):
        """Root, or the canonical schedule of its parent idea."""
        if self.is_root():
            return True
        idea = self.parent_idea()
        return idea is not None and idea.canonical == self.full_id

    # -- result ----------------------------------------------------------
    @property
    def result(self):
        if self._result is _UNLOADED:
            p = os.path.join(self.dir, "result.txt")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    self._result = f.read().strip()
            else:
                self._result = "c++ error"  # default (worst)
        return self._result

    def set_result(self, value):
        assert value in ("c++ error", "halide error", "success")
        self._result = value
        self._result_dirty = True
        self.catalog._mark_dirty(self)

    # -- commentary ------------------------------------------------------
    @property
    def commentary(self):
        if self._commentary is _UNLOADED:
            self._commentary = []
            cdir = self.comment_dir
            if os.path.isdir(cdir):
                for name in os.listdir(cdir):
                    if not name.endswith(".txt"):
                        continue
                    stem = name[:-len(".txt")]
                    # Filename is {ts}.txt or {ts}_{importance}.txt.  The ts
                    # itself contains '_', so split off a trailing _<int> only.
                    importance = None
                    base = stem
                    idx = stem.rfind("_")
                    if idx != -1:
                        tail = stem[idx + 1:]
                        try:
                            importance = int(tail)
                            base = stem[:idx]
                        except ValueError:
                            importance = None
                            base = stem
                    # Guard: base must be a valid timestamp; else treat whole
                    # stem as timestamp with no importance.
                    if not ids.is_timestamp(base):
                        base, importance = stem, None
                    self._commentary.append(
                        Commentary(self, base, importance))
        return self._commentary

    def add_commentary(self, text, importance=None):
        def build_path(t):
            if importance is None:
                fn = "{}.txt".format(t)
            else:
                fn = "{}_{:d}.txt".format(t, importance)
            return os.path.join(self.comment_dir, fn)
        ts = self.catalog.mint_timestamped_name(build_path)
        c = Commentary(self, ts, importance, text=text, is_new=True)
        # Ensure list is loaded then append so subsequent reads see it.
        self.commentary.append(c)
        return c

    # -- benchmarks ------------------------------------------------------
    @property
    def benchmarks(self):
        if self._benchmarks is _UNLOADED:
            self._benchmarks = []
            bdir = self.bench_dir
            if os.path.isdir(bdir):
                for name in sorted(os.listdir(bdir)):
                    if name.endswith(".json"):
                        self._benchmarks.append(Benchmark(self, name))
        return self._benchmarks

    def add_benchmark(self, hostname, data):
        ts = self.catalog.mint_timestamped_name(
            lambda t: os.path.join(self.bench_dir,
                                   "{}_{}.json".format(hostname, t)))
        filename = "{}_{}.json".format(hostname, ts)
        b = Benchmark(self, filename, data=data, is_new=True)
        self.benchmarks.append(b)
        return b

    # -- flush -----------------------------------------------------------
    def flush(self):
        if self.is_new:
            safety.makedirs_tracked(self.dir)
            safety.new_file(os.path.join(self.dir, "generator.cpp"), self.source)
            if self.parent_id is not None:
                safety.new_file(os.path.join(self.dir, "parent.txt"),
                                self.parent_id + "\n")
        else:
            # An existing root schedule that gained a parent (force_parent_idea):
            if self._parent_id_added:
                safety.new_file(os.path.join(self.dir, "parent.txt"),
                                self._parent_id + "\n")
            # fix_canonical re-parented an existing node (overwrite):
            if self._parent_id_overwritten:
                safety.queue_overwrite(os.path.join(self.dir, "parent.txt"),
                                       self._parent_id + "\n")
        # result.txt: write_allowed picks new-file (new node) or deferred
        # overwrite (existing node) automatically.
        if self._result_dirty:
            safety.write_allowed(os.path.join(self.dir, "result.txt"),
                                 self._result + "\n")

    # force_parent_idea sets this on an existing root node.
    _parent_id_added = False
    # fix_canonical re-parents an existing (non-root) node: OVERWRITES parent.txt.
    _parent_id_overwritten = False

    def set_parent_existing_root(self, idea_id):
        assert not self.is_new
        self._parent_id = idea_id
        self._parent_id_added = True
        self.catalog._mark_dirty(self)

    def set_parent_overwrite(self, idea_id):
        """Re-parent an existing node by overwriting parent.txt.  Only used by
        fix_canonical (see note in impl.md safety rules -- parent.txt is not in
        the documented overwrite-allowed list; flagged for author review)."""
        assert not self.is_new
        self._parent_id = idea_id
        self._parent_id_overwritten = True
        self.catalog._mark_dirty(self)


# ---------------------------------------------------------------------------
# Idea node
# ---------------------------------------------------------------------------

class IdeaNode:
    def __init__(self, catalog, full_id, is_new=False, proposal_text=None):
        self.catalog = catalog
        self.full_id = full_id
        self.is_new = is_new
        self._proposal_text = (proposal_text if proposal_text is not None
                               else _UNLOADED)
        self._canonical = _UNLOADED       # _UNLOADED / None (=no canonical) / schedule id str
        self._canonical_dirty = False
        # Derived child edges (filled by Catalog._ensure_linked):
        self.child_schedule_ids = None
        if is_new:
            self.catalog._mark_dirty(self)

    @property
    def proposal_name(self):
        return ids.idea_proposal_name(self.full_id)

    @property
    def parent_id(self):
        return ids.idea_parent_id(self.full_id)

    @property
    def timestamp(self):
        # An idea's implicit timestamp is its parent schedule's timestamp.
        return ids.schedule_timestamp(self.parent_id)

    def parent_schedule(self):
        return self.catalog.get_schedule(self.parent_id)

    @property
    def dir(self):
        return os.path.join(self.catalog.idea_dir, self.full_id)

    @property
    def proposal_text(self):
        if self._proposal_text is _UNLOADED:
            with open(os.path.join(self.dir, "proposal.txt"), "r",
                      encoding="utf-8") as f:
                self._proposal_text = f.read()
        return self._proposal_text

    # -- canonical schedule (presence/absence file) ----------------------
    @property
    def canonical(self):
        """Full ID of the canonical schedule, or None if there isn't one.

        Encoded on disk purely by the presence/absence of canonical.txt, so
        this is a tri-state field: _UNLOADED (haven't checked) vs. None
        (checked, file absent => no canonical) vs. the loaded ID string."""
        if self._canonical is _UNLOADED:
            p = os.path.join(self.dir, "canonical.txt")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    self._canonical = f.read().strip()
            else:
                self._canonical = None
        return self._canonical

    def canonical_lines(self):
        """Raw stripped lines of canonical.txt (for fix_canonical, which must
        cope with a merge-conflicted file holding two IDs)."""
        p = os.path.join(self.dir, "canonical.txt")
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]

    def set_canonical(self, schedule_id):
        self._canonical = schedule_id
        self._canonical_dirty = True
        self.catalog._mark_dirty(self)

    @property
    def importance(self):
        """Derived; float('-inf') if no canonical schedule, else max of the
        canonical schedule's commentary importances (0 if there are none)."""
        canon_id = self.canonical
        if canon_id is None:
            return float("-inf")
        canon = self.catalog.get_schedule(canon_id)
        imps = [c.importance for c in canon.commentary if c.importance is not None]
        if not imps:
            return 0
        return max(imps)

    def flush(self):
        if self.is_new:
            safety.makedirs_tracked(self.dir)
            safety.new_file(os.path.join(self.dir, "proposal.txt"),
                            self.proposal_text)
        # canonical.txt: same in both cases; write_allowed picks new-file vs
        # deferred overwrite automatically.
        if self._canonical_dirty and self._canonical is not None:
            safety.write_allowed(os.path.join(self.dir, "canonical.txt"),
                                 self._canonical + "\n")


# ---------------------------------------------------------------------------
# Session node
# ---------------------------------------------------------------------------

class SessionNode:
    """One agent session.  On disk: session/{id}/ with seed_idea.txt (required),
    parent.txt (optional), output_schedule.txt (optional), delisted.txt
    (presence flag).  Depth/timestamp are derived from the ID.  The gitignored
    private workspace (generator.cpp, current_idea_state.txt, bin/) lives
    separately under private/{id}/ and is NOT owned by this node."""

    def __init__(self, catalog, full_id, is_new=False,
                 seed_idea_id=None, parent_id=None):
        self.catalog = catalog
        self.full_id = full_id
        self.is_new = is_new
        self._seed_idea_id = seed_idea_id if seed_idea_id is not None else _UNLOADED
        # parent tri-state: _UNLOADED / None (=no parent) / id str.  For a new
        # node we know it directly (parent_id or None).
        self._parent_id = parent_id if is_new else _UNLOADED
        self._output_schedule_id = _UNLOADED   # _UNLOADED / None / id str
        self._output_dirty = False
        self._delisted = _UNLOADED             # _UNLOADED / bool
        self._delisted_dirty = False
        # Derived child sessions (filled by Catalog session linking; Phase 4).
        self.child_session_ids = None
        if is_new:
            self.catalog._mark_dirty(self)

    # -- identity --------------------------------------------------------
    @property
    def depth(self):
        return ids.session_depth(self.full_id)

    @property
    def timestamp(self):
        return ids.session_timestamp(self.full_id)

    @property
    def dir(self):
        return os.path.join(self.catalog.session_dir, self.full_id)

    @property
    def private_dir(self):
        return self.catalog.session_private_dir(self.full_id)

    # -- seed idea (required) -------------------------------------------
    @property
    def seed_idea_id(self):
        if self._seed_idea_id is _UNLOADED:
            with open(os.path.join(self.dir, "seed_idea.txt"), "r",
                      encoding="utf-8") as f:
                self._seed_idea_id = f.read().strip()
        return self._seed_idea_id

    # -- parent session (optional) --------------------------------------
    @property
    def parent_id(self):
        if self._parent_id is _UNLOADED:
            p = os.path.join(self.dir, "parent.txt")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    self._parent_id = f.read().strip()
            else:
                self._parent_id = None
        return self._parent_id

    # -- output schedule (optional) -------------------------------------
    @property
    def output_schedule_id(self):
        if self._output_schedule_id is _UNLOADED:
            p = os.path.join(self.dir, "output_schedule.txt")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    self._output_schedule_id = f.read().strip()
            else:
                self._output_schedule_id = None
        return self._output_schedule_id

    def set_output_schedule(self, schedule_id):
        if self.output_schedule_id is not None:
            raise DhHlError("session already has an output schedule")
        self._output_schedule_id = schedule_id
        self._output_dirty = True
        self.catalog._mark_dirty(self)

    # -- delisted flag (presence) ---------------------------------------
    @property
    def delisted(self):
        if self._delisted is _UNLOADED:
            self._delisted = os.path.exists(
                os.path.join(self.dir, "delisted.txt"))
        return self._delisted

    def set_delisted(self):
        self._delisted = True
        self._delisted_dirty = True
        self.catalog._mark_dirty(self)

    # -- derived: self-closed -------------------------------------------
    def is_self_closed(self):
        return self.output_schedule_id is not None or self.delisted

    # -- flush -----------------------------------------------------------
    def flush(self):
        if self.is_new:
            safety.makedirs_tracked(self.dir)
            safety.new_file(os.path.join(self.dir, "seed_idea.txt"),
                            self.seed_idea_id + "\n")
            if self._parent_id is not None:
                safety.new_file(os.path.join(self.dir, "parent.txt"),
                                self._parent_id + "\n")
        # output_schedule.txt / delisted.txt are presence/pointer files added
        # once and never modified -- created new whether the session node is new
        # or pre-existing (close_session / delist on an existing session).
        if self._output_dirty and self._output_schedule_id is not None:
            safety.new_file(os.path.join(self.dir, "output_schedule.txt"),
                            self._output_schedule_id + "\n")
        if self._delisted_dirty and self._delisted:
            safety.new_file(os.path.join(self.dir, "delisted.txt"), "")


# ---------------------------------------------------------------------------
# Current idea state
# ---------------------------------------------------------------------------

class CurrentIdeaState:
    """Parsed current_idea_state.txt.  Never raises on parse; competing/absent
    states are recorded and surfaced only when a caller needs a definite state.

    kind is one of: 'missing', 'no_idea', 'idea', 'conflict'.
    """

    def __init__(self, private_dir, catalog=None):
        # catalog is needed ONLY to mutate (dirty registration + flush), which
        # happens under the catalog lock.  Reading the state (parsing the file)
        # needs no catalog, so the workspace can be read lock-free -- e.g. by
        # build during its pre-catalog-lock compile phase.
        self.private_dir = private_dir
        self.catalog = catalog
        self.kind = None
        self.timestamp = None       # for 'no_idea'
        self.idea_id = None         # for 'idea'
        self.parsed_lines = []      # canonical re-encoded strings of every
                                    # valid state found (for conflict reporting)
        self._dirty = False
        self._load()

    @property
    def path(self):
        return os.path.join(self.private_dir, "current_idea_state.txt")

    def problem_message(self):
        """Human-readable explanation when kind is 'missing' or 'conflict'."""
        if self.kind == "missing":
            return "current_idea_state.txt is missing"
        msg = ["current_idea_state.txt does not encode a single state."]
        if self.parsed_lines:
            msg.append("Competing states found:")
            msg.extend("  " + ln for ln in self.parsed_lines)
        else:
            msg.append("No valid state could be parsed.")
        msg.append("Suggestion: use `dh_hl new_root` to recover.")
        return "\n".join(msg)

    def _load(self):
        if not os.path.exists(self.path):
            self.kind = "missing"
            return
        found = []  # list of ('no_idea', ts) or ('idea', id)
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                v = _parse_state_line(line)
                if v is not None:
                    found.append(v)
        self.parsed_lines = [_encode_state(v) for v in found]
        if len(found) == 0:
            self.kind = "conflict"  # file exists but nothing parsed = cruft
        elif len(found) == 1:
            v = found[0]
            if v[0] == "no_idea":
                self.kind, self.timestamp = "no_idea", v[1]
            else:
                self.kind, self.idea_id = "idea", v[1]
        else:
            self.kind = "conflict"

    def _require_catalog(self):
        if self.catalog is None:
            raise DhHlError(
                "cannot mutate current idea state without a locked catalog")

    def set_no_idea(self, timestamp):
        self._require_catalog()
        self.kind, self.timestamp, self.idea_id = "no_idea", timestamp, None
        self._dirty = True
        self.catalog._mark_dirty(self)

    def set_idea(self, idea_id):
        self._require_catalog()
        self.kind, self.idea_id, self.timestamp = "idea", idea_id, None
        self._dirty = True
        self.catalog._mark_dirty(self)

    def encode(self):
        if self.kind == "no_idea":
            return _encode_state(("no_idea", self.timestamp))
        if self.kind == "idea":
            return _encode_state(("idea", self.idea_id))
        raise DhHlError("cannot encode current idea state of kind " + str(self.kind))

    def flush(self):
        if self._dirty:
            # The private dir is guaranteed to exist by the time we flush: every
            # current-idea-state mutation goes through a tool that first calls
            # SessionWorkspace.ensure_private_dir() (and session-lock tools also
            # created it in locks.acquire_session).  write_allowed picks a
            # new-file create or a deferred overwrite automatically.
            safety.write_allowed(self.path, self.encode() + "\n")


def _parse_state_line(line):
    """Return ('no_idea', ts) / ('idea', idea_id) / None (cruft)."""
    inner = _unwrap(line, "dendritic_hl_root")
    if inner is not None and ids.is_timestamp(inner):
        return ("no_idea", inner)
    inner = _unwrap(line, "dendritic_hl_idea")
    if inner is not None and ids.is_idea_id(inner):
        return ("idea", inner)
    return None


def _unwrap(line, name):
    prefix = name + "("
    if line.startswith(prefix) and line.endswith(")"):
        return line[len(prefix):-1]
    return None


def _encode_state(v):
    if v[0] == "no_idea":
        return "dendritic_hl_root({})".format(v[1])
    return "dendritic_hl_idea({})".format(v[1])


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class Catalog:
    def __init__(self, catalog_dir):
        # Absolutize once, here at the boundary where paths enter the model, so
        # nothing downstream can hit a relative-vs-absolute mismatch across a
        # subprocess cwd change (e.g. the profiler JSON path handed to a child
        # running in a session bin dir).  Everything derived below is absolute.
        self.catalog_dir = os.path.abspath(catalog_dir)
        # LOAD-BEARING INVARIANT: possessing a Catalog (or any of its
        # sub-objects) guarantees the catalog lock is held *for this catalog* --
        # the caller (context.py / build.py) must acquire it first.  The catalog
        # lock is never released mid-process (only at exit), so the guarantee
        # holds for the object's whole lifetime.  See impl.md "Tool Safety: Lock
        # Hierarchy".  (Tests fake the lock state via locks._fake_hold_for_tests
        # or the real acquire path with flock monkeypatched; the invariant is
        # never simply skipped.)
        assert locks.catalog_lock_held() \
            and locks.locked_catalog_dir() == self.catalog_dir, \
            "Catalog constructed without holding its catalog lock: " \
            + self.catalog_dir
        self.sch_dir = os.path.join(self.catalog_dir, "sch")
        self.idea_dir = os.path.join(self.catalog_dir, "idea")
        self.session_dir = os.path.join(self.catalog_dir, "session")
        self.private_dir = os.path.join(self.catalog_dir, "private")
        self._schedules = None
        self._ideas = None
        self._sessions = None
        self._linked = False
        self._session_linked = False
        self._dirty = {}            # id(obj) -> obj
        self._last_timestamp = None  # for fresh_timestamp monotonicity

    def exists(self):
        return os.path.isdir(self.catalog_dir)

    def session_private_dir(self, session_id):
        """The gitignored private-workspace dir for *session_id* (lock-free;
        may not yet exist -- callers initialize it lazily)."""
        return os.path.join(self.private_dir, session_id)

    # -- timestamps ------------------------------------------------------
    def fresh_timestamp(self):
        """A timestamp strictly greater than any previously handed out this
        run (busy-wait past duplicates so names never collide)."""
        ts = ids.now_timestamp()
        while self._last_timestamp is not None and ts <= self._last_timestamp:
            ts = ids.now_timestamp()
        self._last_timestamp = ts
        return ts

    def mint_timestamped_name(self, build_path):
        """Return a fresh timestamp string whose derived catalog path does not
        already exist on disk, re-minting on collision.

        *build_path* maps a candidate timestamp to the absolute path whose
        uniqueness must be guaranteed (a schedule/session dir, a commentary
        file, a benchmark file, ...).  This is the single mint path for every
        timestamped catalog name (see impl.md "Tool Safety: Timestamp
        Conflicts").  Two guards combine and neither alone suffices: the
        process-local monotonic busy-wait in fresh_timestamp() separates names
        minted within this run (a multi-node creator mints several before any
        flush, so they aren't on disk yet), and the os.path.exists check
        separates them from names another process has already committed.

        Correctness rests on holding the catalog lock continuously across the
        mint and the subsequent O_EXCL create; given that, the create cannot
        collide.  That lock does not exist yet (Phase 1), so today this is only
        the single-writer guard.  Idea nodes are outside this scheme: their ID
        carries no timestamp, so their uniqueness stays the proposal-name
        collision check in create_idea.
        """
        while True:
            ts = self.fresh_timestamp()
            if not os.path.exists(build_path(ts)):
                return ts

    # -- lazy dict loading ----------------------------------------------
    @property
    def schedules(self):
        if self._schedules is None:
            self._schedules = {}
            if os.path.isdir(self.sch_dir):
                for name in os.listdir(self.sch_dir):
                    if ids.is_schedule_id(name):
                        self._schedules[name] = ScheduleNode(self, name)
        return self._schedules

    @property
    def ideas(self):
        if self._ideas is None:
            self._ideas = {}
            if os.path.isdir(self.idea_dir):
                for name in os.listdir(self.idea_dir):
                    if ids.is_idea_id(name):
                        self._ideas[name] = IdeaNode(self, name)
        return self._ideas

    @property
    def sessions(self):
        if self._sessions is None:
            self._sessions = {}
            if os.path.isdir(self.session_dir):
                for name in os.listdir(self.session_dir):
                    if ids.is_session_id(name):
                        self._sessions[name] = SessionNode(self, name)
        return self._sessions

    def get_schedule(self, full_id):
        node = self.schedules.get(full_id)
        if node is None:
            raise DhHlError("no such schedule node: " + full_id)
        return node

    def get_idea(self, full_id):
        node = self.ideas.get(full_id)
        if node is None:
            raise DhHlError("no such idea node: " + full_id)
        return node

    def get_session(self, full_id):
        node = self.sessions.get(full_id)
        if node is None:
            raise DhHlError("no such session node: " + full_id)
        return node

    # -- derived edges ---------------------------------------------------
    def _ensure_linked(self):
        if self._linked:
            return
        for s in self.schedules.values():
            s.child_idea_ids = []
        for i in self.ideas.values():
            i.child_schedule_ids = []
        # schedule -> child ideas (from idea IDs, no file reads)
        for idea in self.ideas.values():
            parent = idea.parent_id
            s = self.schedules.get(parent)
            if s is not None:
                s.child_idea_ids.append(idea.full_id)
        # idea -> child schedules (reads every parent.txt; all-or-nothing)
        for s in self.schedules.values():
            pid = s.parent_id
            if pid is not None:
                idea = self.ideas.get(pid)
                if idea is not None:
                    idea.child_schedule_ids.append(s.full_id)
        self._linked = True

    def child_ideas(self, schedule):
        self._ensure_linked()
        return [self.ideas[i] for i in schedule.child_idea_ids]

    def child_schedules(self, idea):
        self._ensure_linked()
        return [self.schedules[s] for s in idea.child_schedule_ids]

    # -- derived session edges ------------------------------------------
    def _ensure_session_linked(self):
        """Fill each session's child_session_ids (all-or-nothing), from every
        session's parent.txt.  Both sub-session (depth+1) and successor (0<->0)
        children land here; the edge *type* is derived from the depths."""
        if self._session_linked:
            return
        for s in self.sessions.values():
            s.child_session_ids = []
        for s in self.sessions.values():
            pid = s.parent_id
            if pid is not None and pid in self.sessions:
                self.sessions[pid].child_session_ids.append(s.full_id)
        self._session_linked = True

    def child_sessions(self, session):
        self._ensure_session_linked()
        return [self.sessions[c] for c in session.child_session_ids]

    def session_is_closed(self, session):
        """Self-closed (output schedule or delisted), or a sub-session of a
        closed session.  Successor edges do NOT propagate closedness.

        Iterative walk up the sub-session parent chain.  Per the tree-structure
        policy (idea.md), guard against infinite loops on a cooked catalog: every
        step must go to a strictly-older session (the session timestamp
        invariant), so the walk visits each session at most once and always
        terminates.  A step that would violate the invariant is treated as a
        broken edge and stops the walk (we do not diagnose pre-existing
        violations, only avoid looping)."""
        current = session
        while True:
            if current.is_self_closed():
                return True
            pid = current.parent_id
            if pid is None or pid not in self.sessions:
                return False
            parent = self.sessions[pid]
            if parent.depth != current.depth - 1:
                return False  # successor edge or cooked: not a sub-session parent
            if not (parent.timestamp < current.timestamp):
                return False  # invariant violated -> stop rather than loop
            current = parent

    def session_is_terminus(self, session):
        """Top-level (depth 0), not delisted, and no successor sessions."""
        if session.depth != 0 or session.delisted:
            return False
        for child in self.child_sessions(session):
            if child.depth == 0:  # a successor session
                return False
        return True

    # -- edge mutation w/ invariant checks ------------------------------
    def link_new_child_schedule(self, idea, schedule):
        """Make (new) *schedule* a child of *idea*, enforcing invariants.

        Invariant: idea's parent schedule must be strictly older than the
        child schedule.  (The 'parent of idea is a major schedule' invariant is
        enforced when the idea is created.)
        """
        if idea.timestamp >= schedule.timestamp:
            raise DhHlError(
                "tree invariant violation: idea's parent schedule "
                "(timestamp {}) is not older than child schedule (timestamp {})"
                .format(idea.timestamp, schedule.timestamp))
        schedule._parent_id = idea.full_id  # new node; written in flush
        # keep derived edges consistent if already linked
        if self._linked:
            schedule.child_idea_ids = schedule.child_idea_ids or []
            idea.child_schedule_ids.append(schedule.full_id)

    def create_schedule(self, source, parent_idea=None):
        """Create a brand-new schedule node holding *source* (a str).  If
        parent_idea is given, link it (checking invariants); else it's a root."""
        h = ids.sha256_hex(source)
        ts = self.mint_timestamped_name(
            lambda t: os.path.join(self.sch_dir, ids.make_schedule_id(t, h)))
        full_id = ids.make_schedule_id(ts, h)
        node = ScheduleNode(self, full_id, is_new=True, source=source)
        node._parent_id = None
        node._result_dirty = False
        self.schedules[full_id] = node
        if self._linked:
            node.child_idea_ids = []
        if parent_idea is not None:
            self.link_new_child_schedule(parent_idea, node)
        return node

    def reparent_existing_schedule(self, idea, schedule):
        """Re-parent an EXISTING schedule under *idea* (overwrites parent.txt).
        Enforces the timestamp invariant.  Used only by fix_canonical."""
        if idea.timestamp >= schedule.timestamp:
            raise DhHlError(
                "tree invariant violation: idea's parent schedule (timestamp "
                "{}) is not older than child schedule (timestamp {})".format(
                    idea.timestamp, schedule.timestamp))
        old_parent = schedule.parent_id
        schedule.set_parent_overwrite(idea.full_id)
        if self._linked:
            if old_parent in self.ideas:
                op = self.ideas[old_parent]
                if schedule.full_id in op.child_schedule_ids:
                    op.child_schedule_ids.remove(schedule.full_id)
            idea.child_schedule_ids.append(schedule.full_id)

    def create_idea(self, parent_schedule, proposal_name, proposal_text):
        if not ids.is_proposal_name(proposal_name):
            raise DhHlError(
                "proposal name must be 1..72 chars of [A-Za-z0-9_]: "
                + repr(proposal_name))
        if not parent_schedule.is_major():
            raise DhHlError(
                "parent of an idea must be a major schedule (root or a canonical schedule);\n"
                "{} is minor\n"
                "Advice: consider the `dh_hl canon` tool,\n"
                "if you're satisfied the current schedule implements the current idea".format(
                    self.format_schedule_id(parent_schedule)))
        full_id = ids.make_idea_id(proposal_name, parent_schedule.full_id)
        if full_id in self.ideas:
            raise DhHlError(
                "proposal name {!r} already used under this schedule".format(
                    proposal_name))
        node = IdeaNode(self, full_id, is_new=True, proposal_text=proposal_text)
        self.ideas[full_id] = node
        if self._linked:
            node.child_schedule_ids = []
            parent_schedule.child_idea_ids.append(full_id)
        return node

    def mint_session_id(self, depth):
        """Mint a fresh session ID at *depth*.  Separate from create_session so
        a caller can put the ID into the seed idea's proposal text before the
        session node exists (see the Session Creation Common flow)."""
        try:
            username = getpass.getuser()
        except Exception:
            username = "user"
        hostname = socket.gethostname()
        ts = self.mint_timestamped_name(
            lambda t: os.path.join(
                self.session_dir,
                ids.make_session_id(depth, t, username, hostname)))
        return ids.make_session_id(depth, ts, username, hostname)

    def create_session(self, seed_idea, parent_session, depth, session_id=None):
        """Create a new session node seeded with *seed_idea* at *depth* (0 for
        top-level).  Model-level primitive; the CLI session-creation tools wrap
        this and also initialize the private workspace.  *session_id* may be a
        pre-minted ID (mint_session_id); otherwise one is minted here.

        Enforces the session timestamp invariant when there is a parent: the
        parent session must be strictly older than the child."""
        if session_id is None:
            session_id = self.mint_session_id(depth)
        parent_id = None
        if parent_session is not None:
            if parent_session.timestamp >= ids.session_timestamp(session_id):
                raise DhHlError(
                    "tree invariant violation: parent session (timestamp {}) "
                    "is not older than the new session (timestamp {})".format(
                        parent_session.timestamp,
                        ids.session_timestamp(session_id)))
            parent_id = parent_session.full_id
        node = SessionNode(self, session_id, is_new=True,
                           seed_idea_id=seed_idea.full_id, parent_id=parent_id)
        self.sessions[session_id] = node
        if self._session_linked:
            node.child_session_ids = []
            if parent_id is not None and parent_id in self.sessions:
                self.sessions[parent_id].child_session_ids.append(session_id)
        return node

    # -- dirty / flush ---------------------------------------------------
    def _mark_dirty(self, obj):
        self._dirty[id(obj)] = obj

    def ensure_created(self):
        """Create the catalog directory skeleton if absent."""
        safety.makedirs_tracked(self.sch_dir)
        safety.makedirs_tracked(self.idea_dir)
        safety.makedirs_tracked(self.session_dir)
        gi = os.path.join(self.catalog_dir, ".gitignore")
        if not os.path.exists(gi):
            # The whole per-session private workspace tree is gitignored; only
            # the catalog graph is meant to be checked in.
            safety.new_file(gi, "private\n")

    def flush(self):
        # Correctness of the mint scheme rests on the catalog lock being held
        # continuously through flush (see impl.md "Tool Safety: Timestamp
        # Conflicts").  Same load-bearing invariant as __init__, re-checked here.
        assert locks.catalog_lock_held() \
            and locks.locked_catalog_dir() == self.catalog_dir, \
            "Catalog.flush() without holding the catalog lock: " + self.catalog_dir
        # Single pass: each object creates its new files (immediate) and/or
        # queues its overwrites (deferred to safety.commit()).  The new-before-
        # overwrite physical ordering is guaranteed by safety, not by this loop.
        for o in list(self._dirty.values()):
            o.flush()

    # -- ID formatting (short IDs) --------------------------------------
    def format_schedule_id(self, node):
        return _format_schedule_short(self, node)

    def format_idea_id(self, node):
        return _format_idea_short(self, node)

    # -- ID resolution ---------------------------------------------------
    def resolve_schedule(self, s):
        return _resolve_schedule(self, s)

    def resolve_idea(self, s):
        return _resolve_idea(self, s)


# ---------------------------------------------------------------------------
# Short ID resolution
# ---------------------------------------------------------------------------

def _ambiguous(catalog, matches, kind):
    """Build a DhHlError listing matches oldest-to-newest."""
    ordered = sorted(matches, key=lambda n: n.timestamp)
    lines = ["ambiguous {} ID; matches:".format(kind)]
    if kind == "schedule":
        lines += ["  " + n.full_id for n in ordered]
    else:
        lines += ["  " + n.full_id for n in ordered]
    return DhHlError("\n".join(lines))


def _match_ideas(catalog, hash_prefix, name_prefix):
    if not all(c in "0123456789abcdef" for c in hash_prefix):
        return []
    out = []
    for idea in catalog.ideas.values():
        if ids.schedule_hash(idea.parent_id).startswith(hash_prefix) \
                and idea.proposal_name.startswith(name_prefix):
            out.append(idea)
    return out


def _resolve_idea(catalog, s):
    # Full ID?
    if ids.is_idea_id(s):
        node = catalog.ideas.get(s)
        if node is None:
            raise DhHlError("no such idea node: " + s)
        return node
    # Short: {hash prefix}.{proposal name prefix}
    if "." not in s:
        raise DhHlError("not a valid idea ID: " + repr(s))
    hp, _, pp = s.partition(".")
    if "." in pp:
        raise DhHlError("not a valid idea short ID: " + repr(s))
    matches = _match_ideas(catalog, hp, pp)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise DhHlError("no idea node matches short ID: " + repr(s))
    raise _ambiguous(catalog, matches, "idea")


def _resolve_schedule(catalog, s):
    # Full ID?
    if ids.is_schedule_id(s):
        node = catalog.schedules.get(s)
        if node is None:
            raise DhHlError("no such schedule node: " + s)
        return node
    matches = _match_schedules(catalog, s)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise DhHlError("no schedule node matches short ID: " + repr(s))
    raise _ambiguous(catalog, matches, "schedule")


def _is_hex(s):
    return len(s) > 0 and all(c in "0123456789abcdef" for c in s)


def _match_schedules(catalog, s):
    parts = s.split(".")
    # Form: bare {hash prefix} (hex only, nonempty)
    if len(parts) == 1:
        if not _is_hex(parts[0]):
            raise DhHlError("not a valid schedule ID: " + repr(s))
        return [n for n in catalog.schedules.values()
                if n.hash.startswith(parts[0])]
    # Form: root.{hash prefix}
    if parts[0] == "root" and len(parts) == 2:
        hp = parts[1]
        if not _is_hex(hp):
            raise DhHlError("root.<hash prefix> needs a nonempty hex prefix: "
                            + repr(s))
        return [n for n in catalog.schedules.values()
                if n.is_root() and n.hash.startswith(hp)]
    # Form: {idea short id}.canon  or  {idea short id}.{hash prefix}
    idea_part, _, last = s.rpartition(".")
    ideas = _resolve_idea_matches_lenient(catalog, idea_part)
    out = []
    if last == "canon":
        for idea in ideas:
            c = idea.canonical
            if c is not None:
                out.append(catalog.schedules[c])
    else:
        if not _is_hex(last) and last != "":
            raise DhHlError("not a valid schedule short ID: " + repr(s))
        for idea in ideas:
            for sch in catalog.child_schedules(idea):
                if sch.hash.startswith(last):
                    out.append(sch)
    # dedupe by full_id
    seen, uniq = set(), []
    for n in out:
        if n.full_id not in seen:
            seen.add(n.full_id)
            uniq.append(n)
    return uniq


def _resolve_idea_matches_lenient(catalog, idea_part):
    """Like _resolve_idea but returns *all* matches (no single-match error);
    accepts a full idea ID or a {hp}.{pp} short form."""
    if ids.is_idea_id(idea_part):
        node = catalog.ideas.get(idea_part)
        return [node] if node is not None else []
    hp, dot, pp = idea_part.partition(".")
    if dot == "" or "." in pp:
        return []
    return _match_ideas(catalog, hp, pp)


# ---------------------------------------------------------------------------
# Short ID formatting (output)
# ---------------------------------------------------------------------------

_MIN_HASH = 6


def _format_idea_short(catalog, idea):
    parent_hash = ids.schedule_hash(idea.parent_id)
    name = idea.proposal_name
    for hlen in range(_MIN_HASH, len(parent_hash) + 1):
        cand = "{}.{}".format(parent_hash[:hlen], name)
        try:
            if catalog.resolve_idea(cand).full_id == idea.full_id:
                return cand
        except DhHlError:
            pass
    return idea.full_id  # ambiguous even at full hash: fall back


def _format_schedule_short(catalog, node):
    def good_candidate(catalog, cand):
        # Internal helper: checks candidate schedule short ID and see if
        # it unambiguously resolves to the full ID we're shortening.
        # Silently convert "ambiguous" errors to False.
        try:
            return catalog.resolve_schedule(cand).full_id == node.full_id
        except DhHlError:
            return False

    h = node.hash
    if node.is_root():
        for hlen in range(_MIN_HASH, len(h) + 1):
            cand = "root.{}".format(h[:hlen])
            if good_candidate(catalog, cand):
                return cand
        return node.full_id
    idea = node.parent_idea()
    idea_short = _format_idea_short(catalog, idea)
    if "." not in idea_short and not ids.is_idea_id(idea_short):
        return node.full_id
    # Prefer .canon when applicable.
    if idea.canonical == node.full_id:
        cand = "{}.canon".format(idea_short)
        if good_candidate(catalog, cand):
            return cand
    for hlen in range(_MIN_HASH, len(h) + 1):
        cand = "{}.{}".format(idea_short, h[:hlen])
        if good_candidate(catalog, cand):
            return cand
    return node.full_id
