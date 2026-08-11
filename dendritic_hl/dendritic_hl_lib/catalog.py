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
  NOT provided by flush() -- it is guaranteed by the safety module: an exclusive
  new_file writes immediately, while new_file(overwrite_allowed=True)/
  queue_overwrite defer the overwrite to
  safety.commit(), which Context.finish() runs strictly after the whole flush
  loop.  So flush() may create + queue in any object order safely (each on-disk
  file is owned by exactly one object, so there is never a cross-object race).
"""

import getpass
import json
import os
import sys

from . import ids
from . import locks
from . import profiler_warnings
from . import safety
from .enums import (COMMENTARY_REVIEWS, IdeaStateKind, ProblemState, Result,
                    Review, SideLink)
from .errors import DhHlError, HarnessError

# The profiler JSON schema version this harness understands (the `profiler_version`
# field stamped into every pipeline object; see reference_build_commands.md and
# src/runtime/profiler_common.cpp).  Cost tooling compares only benchmarks at this
# version -- a benchmark set cached at any other version is a "can't compare"
# record and is skipped by the cost core (see cost.py / impl.md "Private Benchmark
# Sets on Disk").  Bump this if the profiler schema/semantics change.
EXPECTED_PROFILER_VERSION = 1

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

# The enum-valued vocabularies (Review, SideLink, Result, ProblemState,
# IdeaStateKind) now live in enums.py; COMMENTARY_REVIEWS (the subset of Review a
# single commentary may carry) is re-exported from there.  Result members are
# declared worst -> best, so their definition order is the ranking below.
_RESULT_RANK = {state: i for i, state in enumerate(Result)}


def best_result(a, b):
    """The better (higher-ranked) of two Result members (idea.md "Schedule Node
    State").  "success" means every Halide binary was BUILT (the generators
    emitted); whether a runner then executes is a per-problem benchmark fact, not
    a node result state."""
    return a if _RESULT_RANK[a] >= _RESULT_RANK[b] else b


def canonical_block_advice(catalog, canonical_id):
    """Shared advice for tools that must refuse because the current idea already
    has a canonical schedule: `canon` and `init_build --target workspace` (idea.md
    "Canon Tool", "Init-Build Tool").  *canonical_id* is the blocking canonical
    schedule's full ID; the message names its short ID and steers to `new_idea` /
    `set_idea` to branch a new idea off it."""
    blocker = catalog.format_schedule_id(catalog.schedules[canonical_id])
    return (
        "idea already has a canonical schedule ({0}).\n"
        "To record the current schedule as a variation, branch a new idea "
        "off that canonical schedule and explore under it:\n"
        "    dh_hl new_idea <name> <proposal file> {0}\n"
        "    dh_hl set_idea <the new idea's ID>\n"
        "then rebuild and `dh_hl canon`.".format(blocker))


# A schedule node's generator parameters are a JSON *list* of parameter objects
# (idea.md "Schedule Node State" / "Generator Parameters JSON Object Format"),
# stored verbatim in generator_parameters.json and hashed (with generator.cpp)
# into the node's ID.  The default when none is supplied is a single empty
# object: "benchmark once with no parameters".
DEFAULT_PARAMETERS = [{}]


def dump_parameters(params):
    """Canonical serialization of a generator-parameters list to file text.

    Used for programmatically created nodes (defaults, session duplicates) so
    identical logical parameters yield identical bytes -- and therefore an
    identical content hash -- everywhere.  Agent-authored workspace files are
    instead copied verbatim, so their exact bytes are what get hashed."""
    return json.dumps(params, indent=1) + "\n"


def validate_parameters(params):
    """Validate a parsed generator-parameters value: a JSON list of objects,
    each mapping names to bool/number/string (idea.md "Generator Parameters JSON
    Object Format").  Raises DhHlError on any violation; returns *params*."""
    if not isinstance(params, list):
        raise DhHlError("generator parameters must be a JSON list of objects")
    for obj in params:
        if not isinstance(obj, dict):
            raise DhHlError(
                "each generator parameters entry must be a JSON object")
        for k, v in obj.items():
            # bool is a subclass of int, so it is allowed implicitly.
            if not isinstance(v, (bool, int, float, str)):
                raise DhHlError(
                    "generator parameter {!r} must be bool/number/string, got "
                    "{}".format(k, type(v).__name__))
    return params


def load_parameters_text(text):
    """Parse+validate generator-parameters file *text*, returning it verbatim
    (so the exact bytes are what get hashed).  Raises DhHlError on bad JSON or
    a shape violation."""
    try:
        params = json.loads(text)
    except ValueError as e:
        raise DhHlError("invalid generator parameters JSON: {}".format(e))
    validate_parameters(params)
    return text


# ---------------------------------------------------------------------------
# Problem objects (idea.md "Problem Object State")
# ---------------------------------------------------------------------------

# A problem object's tri-state enablement is the ProblemState enum (enums.py):
# MAIN is the single default problem for cost tools; ENABLED problems (which
# includes the main) are the ones tested by default.

# Special placeholder tokens allowed in a problem's argv (idea.md "New Problem
# Tool").  <RunGenMain> (only as argv[0]) links a standalone RunGenMain binary;
# <Lib> (only when NOT using <RunGenMain>) is the emitted shared-library path,
# also exported as DENDRITIC_HL_OUTPUT_LIB.
PROBLEM_RUNGENMAIN = "<RunGenMain>"
PROBLEM_LIB = "<Lib>"


def dump_problem_argv(argv):
    """Canonical serialization of a problem's argv list to argv.json text.  The
    sha256 of this exact text is the problem's full ID, so this is the single
    definition shared by hashing and the on-disk file (idea.md "Problem Object
    State")."""
    return json.dumps(argv, indent=1) + "\n"


def validate_problem_argv(argv):
    """Validate a problem's command-line argv (idea.md "New Problem Tool").

    A non-empty list of strings, whose only allowed `<...>` placeholder tokens
    are `<RunGenMain>` (valid only as argv[0]) and `<Lib>` (valid only when
    `<RunGenMain>` is not used).  Raises DhHlError on any violation; returns
    *argv*."""
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise DhHlError("problem argv must be a list of strings")
    if not argv:
        raise DhHlError("a problem needs at least one command-line argument")
    uses_rungenmain = argv[0] == PROBLEM_RUNGENMAIN
    for i, tok in enumerate(argv):
        if not (tok.startswith("<") and tok.endswith(">")):
            continue  # ordinary token
        if tok == PROBLEM_RUNGENMAIN:
            if i != 0:
                raise DhHlError(
                    "<RunGenMain> is only valid as the first argument")
        elif tok == PROBLEM_LIB:
            if uses_rungenmain:
                raise DhHlError("Cannot give both <Lib> and <RunGenMain>")
        else:
            raise DhHlError(
                "unknown special argument {!r}; only <RunGenMain> and <Lib> "
                "are allowed".format(tok))
    return argv


class Commentary:
    """One commentary file: comment/{ts}_{hash}.json (see idea.md "Commentary
    State").  The JSON object holds `text`, `review`, and `cancels`.

    `hash` is the sha256 of the commentary text.  The commentary's *local ID* is
    "{ts}_{hash}" -- exactly the shape of a schedule full ID, so it parses/checks
    with the same ids helpers.  Its *full ID* prepends the parent schedule full
    ID: "{parent schedule full ID}_{ts}_{hash}".  The `cancels` list stores only
    local IDs, since a commentary can only cancel others on the SAME schedule
    node (so the whole cancelled/review derivation needs just this one node)."""

    def __init__(self, schedule, timestamp, comment_hash,
                 text=_UNLOADED, review=None, cancels=None, is_new=False):
        self.schedule = schedule
        self.timestamp = timestamp
        self.hash = comment_hash
        self._text = text
        self._review = review        # None until loaded (never a valid value)
        self._cancels = cancels       # None until loaded
        # A new object already has every field in memory; a disk object loads
        # all three from the single JSON file on first access.
        self._loaded = is_new
        if is_new:
            self.schedule.catalog._mark_dirty(self)

    @property
    def local_id(self):
        return "{}_{}".format(self.timestamp, self.hash)

    @property
    def full_id(self):
        return "{}_{}".format(self.schedule.full_id, self.local_id)

    @property
    def filename(self):
        return "{}.json".format(self.local_id)

    @property
    def path(self):
        return os.path.join(self.schedule.comment_dir, self.filename)

    def _ensure_loaded(self):
        if self._loaded:
            return
        with open(self.path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        self._text = obj.get("text", "")
        # Lenient parse: an unknown/garbage review on disk degrades to NEUTRAL
        # (it simply doesn't count toward the derived review) rather than raising.
        try:
            self._review = Review(obj.get("review", "neutral"))
        except ValueError:
            self._review = Review.NEUTRAL
        self._cancels = list(obj.get("cancels", []))
        self._loaded = True

    @property
    def text(self):
        self._ensure_loaded()
        return self._text

    @property
    def review(self):
        self._ensure_loaded()
        return self._review

    @property
    def cancels(self):
        self._ensure_loaded()
        return self._cancels

    def flush(self):
        safety.makedirs_tracked(self.schedule.comment_dir)
        obj = {"text": self.text, "review": self.review.value,
               "cancels": list(self.cancels)}
        safety.new_file(self.path, json.dumps(obj, indent=1) + "\n")


class Benchmark:
    """One benchmark file: bench/{hostname}_{ts}.json holding a benchmark JSON
    object (see idea.md "Benchmark Sub-object State").

    The file-name stem "{hostname}_{ts}" is the benchmark's *local ID* (hostname
    is the SANITIZED stable hostname).  Its *full ID* prepends the parent schedule
    full ID: "{parent schedule full ID}_{hostname}_{ts}" (idea.md schedule state)."""

    def __init__(self, schedule, filename, data=_UNLOADED, is_new=False):
        self.schedule = schedule
        self.filename = filename
        self._data = data
        if is_new:
            self.schedule.catalog._mark_dirty(self)

    @property
    def local_id(self):
        # Strip the trailing ".json" from the file name.
        return self.filename[:-len(".json")]

    @property
    def full_id(self):
        return "{}_{}".format(self.schedule.full_id, self.local_id)

    @property
    def path(self):
        return os.path.join(self.schedule.bench_dir, self.filename)

    @property
    def data(self):
        if self._data is _UNLOADED:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        return self._data

    @property
    def warnings(self):
        """The profiler warnings captured for this benchmark (idea.md "Benchmark
        JSON Format" `warnings`).  Delegated to profiler_warnings, the single
        chokepoint for the temporary warning-delivery hack."""
        return profiler_warnings.warnings_of_benchmark(self.data)

    @property
    def profiler(self):
        """The stored pipeline stats object (benchmark JSON `profiler`)."""
        return self.data["profiler"]

    @property
    def wall_time_min(self):
        """The raw cost statistic (idea.md "Cost Comparison Methodology"): the
        fastest wall-clock run recorded by the profiler for this benchmark."""
        return self.profiler["wall_time_min"]

    @property
    def profiler_version(self):
        return self.profiler.get("profiler_version")

    @property
    def hostname(self):
        return self.data.get("hostname")

    @property
    def cpu_count(self):
        return self.data.get("cpu_count")

    @property
    def problem(self):
        """Full ID of the problem this benchmark was run with (idea.md
        "Benchmark Sub-object State").  None for pre-problem benchmarks."""
        return self.data.get("problem")

    @property
    def parameters_index(self):
        """Index of the generator-parameters object used (idea.md "Benchmark
        Sub-object State").  None for pre-problem benchmarks."""
        return self.data.get("parameters_index")

    def flush(self):
        safety.makedirs_tracked(self.schedule.bench_dir)
        safety.new_file(self.path, json.dumps(self.data, indent=1) + "\n")


class WarningToggle:
    """One warning-toggle file: warning_toggle/{ts}.json (see idea.md
    "WarningToggle State").  A schedule-node sub-object that either *blocks* a
    profiler warning (a `rule`/`func` pair) or *cancels* another `WarningToggle`
    (re-enabling a blocked warning).  Exactly one of those two forms holds -- the
    on-disk `value` is a tagged union.

    The sub-object's *local ID* is its "{ts}" timestamp; its *full ID* prepends
    the parent schedule full ID: "{parent schedule full ID}_{ts}".  A `cancels`
    reference stores the FULL WarningToggle ID of its target, because unlike
    commentary cancels these may cross schedule nodes (idea.md)."""

    def __init__(self, schedule, timestamp, citation=None, rule=None, func=None,
                 cancels=None, is_new=False):
        self.schedule = schedule
        self.timestamp = timestamp
        self._citation = citation    # full commentary ID (any node in catalog)
        self._rule = rule            # block form: warning rule slug, else None
        self._func = func            # block form: func name, else None
        self._cancels = cancels      # cancel form: full WarningToggle ID, else None
        self._loaded = is_new
        if is_new:
            self.schedule.catalog._mark_dirty(self)

    @property
    def local_id(self):
        return self.timestamp

    @property
    def full_id(self):
        return ids.make_warning_toggle_id(self.schedule.full_id, self.timestamp)

    @property
    def filename(self):
        return "{}.json".format(self.local_id)

    @property
    def path(self):
        return os.path.join(self.schedule.warning_toggle_dir, self.filename)

    def _ensure_loaded(self):
        if self._loaded:
            return
        with open(self.path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        self._citation = obj.get("citation")
        self._rule = obj.get("rule")
        self._func = obj.get("func")
        self._cancels = obj.get("cancels")
        self._loaded = True

    @property
    def citation(self):
        self._ensure_loaded()
        return self._citation

    @property
    def rule(self):
        self._ensure_loaded()
        return self._rule

    @property
    def func(self):
        self._ensure_loaded()
        return self._func

    @property
    def cancels(self):
        self._ensure_loaded()
        return self._cancels

    def is_block(self):
        return self.cancels is None

    def flush(self):
        safety.makedirs_tracked(self.schedule.warning_toggle_dir)
        obj = {"citation": self.citation, "rule": self.rule, "func": self.func,
               "cancels": self.cancels}
        safety.new_file(self.path, json.dumps(obj, indent=1) + "\n")


# ---------------------------------------------------------------------------
# Benchmark set (top-level catalog object, not a schedule sub-object)
# ---------------------------------------------------------------------------

class BenchmarkSet:
    """One benchmark_sets/{id}.json file (see idea.md "Benchmark Set State").

    Full ID is "{sanitized hostname}_{timestamp}" (no short-ID form).  The JSON
    payload is a 3-level structure, `data[schedule full ID][params index][batch]`
    -> benchmark sub-object full ID.  It groups the benchmarks produced by one
    `build --only all --profile N` run so comparison tools can compare only
    within a batch (fighting profiler noise)."""

    def __init__(self, catalog, full_id, data=_UNLOADED, is_new=False):
        self.catalog = catalog
        self.full_id = full_id
        self._data = data
        if is_new:
            self.catalog._mark_dirty(self)

    @property
    def filename(self):
        return self.full_id + ".json"

    @property
    def path(self):
        return os.path.join(self.catalog.benchmark_sets_dir, self.filename)

    @property
    def data(self):
        if self._data is _UNLOADED:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        return self._data

    def flush(self):
        safety.makedirs_tracked(self.catalog.benchmark_sets_dir)
        safety.new_file(self.path, json.dumps(self.data, indent=1) + "\n")


class Problem:
    """One problem object: problem/{full_id}/ holding argv.json, state.txt,
    short_name.txt (idea.md "Problem Object State", impl.md "Problem Objects on
    Disk").

    Full ID is the sha256 of the canonical argv.json text, so the argv is
    immutable (changing it is a different problem).  state and short_name are
    mutable (both in the safety module's overwrite-allowed list)."""

    def __init__(self, catalog, full_id, is_new=False, argv=None,
                 state=ProblemState.ENABLED, short_name=None):
        self.catalog = catalog
        self.full_id = full_id
        self._argv = argv if argv is not None else _UNLOADED
        self._state = state if is_new else _UNLOADED
        self._short_name = short_name if is_new else _UNLOADED
        self._state_dirty = False
        self._short_name_dirty = False
        self.is_new = is_new
        if is_new:
            self.catalog._mark_dirty(self)

    @property
    def dir(self):
        return os.path.join(self.catalog.problem_dir, self.full_id)

    @property
    def argv(self):
        if self._argv is _UNLOADED:
            with open(os.path.join(self.dir, "argv.json"), "r",
                      encoding="utf-8") as f:
                self._argv = json.load(f)
        return self._argv

    @property
    def state(self):
        # A malformed OR missing state.txt reads as "enabled" with a warning
        # (idea.md / impl.md "Problem Objects on Disk"): a well-formed problem
        # always has a valid one, so either is a corruption worth surfacing.
        if self._state is _UNLOADED:
            p = os.path.join(self.dir, "state.txt")
            try:
                with open(p, "r", encoding="utf-8") as f:
                    v = f.read().strip()
            except FileNotFoundError:
                v = None
            try:
                self._state = ProblemState(v)
            except ValueError:
                shown = "missing" if v is None else repr(v)
                print("dh_hl: warning: malformed problem state {} in {}; "
                      "treating as 'enabled'".format(shown, self.full_id),
                      file=sys.stderr)
                self._state = ProblemState.ENABLED
        return self._state

    @property
    def short_name(self):
        if self._short_name is _UNLOADED:
            try:
                with open(os.path.join(self.dir, "short_name.txt"), "r",
                          encoding="utf-8") as f:
                    self._short_name = f.read().strip()
            except FileNotFoundError:
                self._short_name = ""
        return self._short_name

    def is_enabled(self):
        return self.state in (ProblemState.ENABLED, ProblemState.MAIN)

    def set_state(self, value):
        # Unconditional dirty (no "skip if unchanged" short-circuit): every other
        # setter in the model works this way, and comparing against the current
        # value would refuse to heal a malformed state.txt that happens to resolve
        # to the same default (see "A cautionary tale" in Tool Internal Design).
        assert isinstance(value, ProblemState)
        self._state = value
        self._state_dirty = True
        self.catalog._mark_dirty(self)

    def set_short_name(self, value):
        if not ids.is_problem_short_name(value):
            raise DhHlError(
                "problem short name must be 1+ chars of [A-Za-z0-9_]: "
                + repr(value))
        self._short_name = value
        self._short_name_dirty = True
        self.catalog._mark_dirty(self)

    def flush(self):
        # argv.json is immutable (it is the hash preimage), so it is written once
        # at creation.  state.txt / short_name.txt are the mutable files: a new
        # problem writes both, an existing one writes only what a setter dirtied.
        # new_file(overwrite_allowed=True) handles both cases (exclusive create
        # when absent, deferred overwrite when present), so there is one write
        # path, not two.
        if self.is_new:
            safety.makedirs_tracked(self.dir)
            safety.new_file(os.path.join(self.dir, "argv.json"),
                            dump_problem_argv(self._argv))
        if self.is_new or self._state_dirty:
            safety.new_file(os.path.join(self.dir, "state.txt"),
                            self._state.value + "\n", overwrite_allowed=True)
        if self.is_new or self._short_name_dirty:
            safety.new_file(os.path.join(self.dir, "short_name.txt"),
                            self._short_name + "\n", overwrite_allowed=True)


# ---------------------------------------------------------------------------
# Schedule node
# ---------------------------------------------------------------------------

class ScheduleNode:
    def __init__(self, catalog, full_id, is_new=False, source=None,
                 params_text=None):
        self.catalog = catalog
        self.full_id = full_id
        self.is_new = is_new
        self._source = source if source is not None else _UNLOADED
        # Raw text of generator_parameters.json (hashed verbatim into the ID);
        # `parameters` parses it into the JSON list on demand.
        self._params_text = params_text if params_text is not None else _UNLOADED
        self._parent_id = _UNLOADED       # _UNLOADED / None (=root) / parent id str
        self._result = _UNLOADED          # _UNLOADED / result str (absent file => "unknown")
        self._result_dirty = False
        self._commentary = _UNLOADED      # list[Commentary]
        self._benchmarks = _UNLOADED      # list[Benchmark]
        self._warning_toggles = _UNLOADED  # list[WarningToggle]
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

    @property
    def warning_toggle_dir(self):
        return os.path.join(self.dir, "warning_toggle")

    # -- source ----------------------------------------------------------
    @property
    def source(self):
        if self._source is _UNLOADED:
            with open(os.path.join(self.dir, "generator.cpp"), "rb") as f:
                self._source = f.read().decode("utf-8")
        return self._source

    # -- generator parameters --------------------------------------------
    @property
    def params_text(self):
        """Raw text of generator_parameters.json (the bytes that were hashed)."""
        if self._params_text is _UNLOADED:
            with open(os.path.join(self.dir, "generator_parameters.json"),
                      "rb") as f:
                self._params_text = f.read().decode("utf-8")
        return self._params_text

    @property
    def parameters(self):
        """The generator-parameters list (parsed from params_text)."""
        params = json.loads(self.params_text)
        if not isinstance(params, list):
            raise DhHlError(
                "generator_parameters.json must hold a JSON list in "
                + self.full_id)
        return params

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
            try:
                with open(p, "r", encoding="utf-8") as f:
                    v = f.read().strip()
            except FileNotFoundError:
                v = None
            if v is None:
                # No result.txt: no compile attempted yet.  This is the NORMAL
                # unbuilt state, so it is silent (unlike a Problem, whose missing
                # state.txt is abnormal and warns).
                self._result = Result.UNKNOWN
            else:
                try:
                    self._result = Result(v)
                except ValueError:
                    # Malformed (e.g. a merge conflict left markers in
                    # result.txt): degrade to UNKNOWN + warn, mirroring Problem
                    # state.txt leniency.  Safe because best_result only moves a
                    # node UPWARD and canon requires SUCCESS, so a spurious
                    # UNKNOWN is merely pessimistic; a rebuild overwrites it
                    # (set_result always dirties).  Reading it here does NOT
                    # dirty, so the garbage is not silently rewritten.
                    print("dh_hl: warning: malformed schedule result {} in {}; "
                          "treating as 'unknown'".format(repr(v), self.full_id),
                          file=sys.stderr)
                    self._result = Result.UNKNOWN
        return self._result

    def set_result(self, value):
        assert isinstance(value, Result)
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
                    if not name.endswith(".json"):
                        continue  # legacy .txt commentary (pre-review) ignored
                    stem = name[:-len(".json")]
                    # stem is the local ID "{ts}_{hash}" -- same shape as a
                    # schedule full ID, so reuse those parsers/guards.
                    if not ids.looks_like_schedule_id(stem):
                        continue
                    self._commentary.append(
                        Commentary(self, ids.schedule_timestamp(stem),
                                   ids.schedule_hash(stem)))
        return self._commentary

    def add_commentary(self, text, review=Review.NEUTRAL, cancels=None):
        assert review in COMMENTARY_REVIEWS
        h = ids.sha256_hex(text)
        ts = self.catalog.mint_timestamped_name(
            lambda t: os.path.join(self.comment_dir,
                                   "{}_{}.json".format(t, h)))
        c = Commentary(self, ts, h, text=text, review=review,
                       cancels=list(cancels or []), is_new=True)
        # Ensure list is loaded then append so subsequent reads see it.
        self.commentary.append(c)
        return c

    def commentary_cancelled_by(self):
        """Map each commentary local ID -> list of local IDs of same-node
        commentary that cancel it (name it in their `cancels` list).  Derivable
        from this node alone, since cancels are always same-node (idea.md)."""
        by = {c.local_id: [] for c in self.commentary}
        for c in self.commentary:
            for target in c.cancels:
                if target in by:
                    by[target].append(c.local_id)
        return by

    @property
    def review(self):
        """Derived review of this schedule from its NON-cancelled commentary
        (idea.md "Commentary State"): mixed if both a positive and a negative
        are present, else positive / negative / lost_interest / neutral."""
        cancelled = set()
        for c in self.commentary:
            cancelled.update(c.cancels)
        pos = neg = lost = False
        for c in self.commentary:
            if c.local_id in cancelled:
                continue
            r = c.review
            if r is Review.POSITIVE:
                pos = True
            elif r is Review.NEGATIVE:
                neg = True
            elif r is Review.LOST_INTEREST:
                lost = True
        if pos and neg:
            return Review.MIXED
        if pos:
            return Review.POSITIVE
        if neg:
            return Review.NEGATIVE
        if lost:
            return Review.LOST_INTEREST
        return Review.NEUTRAL

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

    # -- warning toggles -------------------------------------------------
    @property
    def warning_toggles(self):
        if self._warning_toggles is _UNLOADED:
            self._warning_toggles = []
            wdir = self.warning_toggle_dir
            if os.path.isdir(wdir):
                for name in os.listdir(wdir):
                    if not name.endswith(".json"):
                        continue
                    stem = name[:-len(".json")]
                    if not ids.is_timestamp(stem):
                        continue
                    self._warning_toggles.append(WarningToggle(self, stem))
        return self._warning_toggles

    def add_warning_toggle(self, citation, rule=None, func=None, cancels=None):
        """Add a WarningToggle blocking (rule, func) OR cancelling another toggle
        (`cancels` = full WarningToggle ID).  Exactly one form, since the on-disk
        value is a tagged union (idea.md "WarningToggle State")."""
        assert (cancels is None) != (rule is None and func is None), \
            "WarningToggle is exactly one of block(rule,func) or cancel"
        ts = self.catalog.mint_timestamped_name(
            lambda t: os.path.join(self.warning_toggle_dir,
                                   "{}.json".format(t)))
        w = WarningToggle(self, ts, citation=citation, rule=rule, func=func,
                          cancels=cancels, is_new=True)
        self.warning_toggles.append(w)
        return w

    # -- flush -----------------------------------------------------------
    def flush(self):
        if self.is_new:
            safety.makedirs_tracked(self.dir)
            safety.new_file(os.path.join(self.dir, "generator.cpp"), self.source)
            safety.new_file(
                os.path.join(self.dir, "generator_parameters.json"),
                self.params_text)
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
        # result.txt: new_file(overwrite_allowed=True) picks exclusive-create
        # (new node) or deferred overwrite (existing node) automatically.
        if self._result_dirty:
            safety.new_file(os.path.join(self.dir, "result.txt"),
                            self._result.value + "\n", overwrite_allowed=True)

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
        self._side_links = _UNLOADED      # _UNLOADED / list[(type, dest_id)]
        self._new_side_links = []         # links added this run (to be flushed)
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
    def review(self):
        """Inherits the review value of the canonical schedule; `neutral` if
        there is no canonical schedule (idea.md "Idea Node State")."""
        canon_id = self.canonical
        if canon_id is None:
            return Review.NEUTRAL
        canon = self.catalog.schedules.get(canon_id)
        if canon is None:
            return Review.NEUTRAL  # dangling canonical (e.g. git checkout desync)
        return canon.review

    # -- idea side links (presence files) --------------------------------
    @property
    def side_links(self):
        """Outgoing idea side links as a list of (SideLink, dest idea full ID).
        Encoded purely by the existence of empty files
        idea/{id}/{type}/{dest} (anti-merge-conflict design).  Read once."""
        if self._side_links is _UNLOADED:
            out = []
            for link_type in SideLink:
                d = os.path.join(self.dir, link_type.value)
                if os.path.isdir(d):
                    for name in os.listdir(d):
                        out.append((link_type, name))
            self._side_links = out
        return self._side_links

    def add_side_link(self, link_type, dest_id):
        """Add an outgoing side link.  Silent no-op (returns False) if it exactly
        duplicates an existing link (same type + destination)."""
        assert isinstance(link_type, SideLink)
        if (link_type, dest_id) in self.side_links:
            return False
        self.side_links.append((link_type, dest_id))
        self._new_side_links.append((link_type, dest_id))
        self.catalog._mark_dirty(self)
        return True

    def flush(self):
        if self.is_new:
            safety.makedirs_tracked(self.dir)
            safety.new_file(os.path.join(self.dir, "proposal.txt"),
                            self.proposal_text)
        # canonical.txt: same in both cases; new_file(overwrite_allowed=True)
        # picks exclusive-create vs deferred overwrite automatically.
        if self._canonical_dirty and self._canonical is not None:
            safety.new_file(os.path.join(self.dir, "canonical.txt"),
                            self._canonical + "\n", overwrite_allowed=True)
        # Side links: one empty presence file per newly added link.
        for link_type, dest_id in self._new_side_links:
            d = os.path.join(self.dir, link_type.value)
            safety.makedirs_tracked(d)
            safety.new_file(os.path.join(d, dest_id), "")


# ---------------------------------------------------------------------------
# Session node
# ---------------------------------------------------------------------------

class SessionNode:
    """One agent session.  On disk: session/{id}/ with:

    * ``prompt.txt`` (required): the session's prompt (plain text).
    * ``seed_ideas.json`` (required): JSON list of seed idea full IDs (>= 1).
    * ``parent.txt`` (optional): parent session full ID.
    * ``default_anchor_schedule.txt`` (optional): a schedule full ID.
    * ``outputs.json`` (optional): the session outputs, absent until closed.
      Shape: ``{"schedules": [{"id": <full id>, "pool_tag": <str>}, ...],
      "benchmark_sets": [<full id>, ...]}``.  The first schedule is the
      *primary* output (idea.md "Session Node State" / "Close Session Tool").
    * ``delisted.txt`` (presence flag).

    Depth/timestamp are derived from the ID.  The gitignored private workspace
    lives under private/{id}/ and is NOT owned by this node."""

    def __init__(self, catalog, full_id, is_new=False, seed_idea_ids=None,
                 prompt=None, parent_id=None, default_anchor_schedule_id=None):
        self.catalog = catalog
        self.full_id = full_id
        self.is_new = is_new
        self._seed_idea_ids = (list(seed_idea_ids)
                               if seed_idea_ids is not None else _UNLOADED)
        self._prompt = prompt if prompt is not None else _UNLOADED
        # parent tri-state: _UNLOADED / None (=no parent) / id str.  For a new
        # node we know it directly (parent_id or None).
        self._parent_id = parent_id if is_new else _UNLOADED
        # default anchor tri-state; for a new node it's the passed value (or None).
        self._default_anchor = default_anchor_schedule_id if is_new else _UNLOADED
        self._outputs = _UNLOADED               # _UNLOADED / None / dict
        self._outputs_dirty = False
        self._delisted = _UNLOADED              # _UNLOADED / bool
        self._delisted_dirty = False
        # Derived child sessions (filled by Catalog session linking).
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

    # -- prompt (required) ----------------------------------------------
    @property
    def prompt(self):
        if self._prompt is _UNLOADED:
            with open(os.path.join(self.dir, "prompt.txt"), "r",
                      encoding="utf-8") as f:
                self._prompt = f.read()
        return self._prompt

    # -- seed ideas (required, >= 1) ------------------------------------
    @property
    def seed_idea_ids(self):
        if self._seed_idea_ids is _UNLOADED:
            with open(os.path.join(self.dir, "seed_ideas.json"), "r",
                      encoding="utf-8") as f:
                self._seed_idea_ids = json.load(f)
        return self._seed_idea_ids

    @property
    def seed_idea_id(self):
        """The 0th seed idea -- the canonical "the session's seed idea" per the
        idea.md 0th-seed rule (copy_seed_schedule / seed_schedule_* tools)."""
        return self.seed_idea_ids[0]

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

    # -- default anchor schedule (optional) -----------------------------
    @property
    def default_anchor_schedule_id(self):
        if self._default_anchor is _UNLOADED:
            p = os.path.join(self.dir, "default_anchor_schedule.txt")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    self._default_anchor = f.read().strip()
            else:
                self._default_anchor = None
        return self._default_anchor

    # -- outputs (optional; absent until closed) ------------------------
    @property
    def _outputs_obj(self):
        if self._outputs is _UNLOADED:
            p = os.path.join(self.dir, "outputs.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    self._outputs = json.load(f)
            else:
                self._outputs = None
        return self._outputs

    def has_outputs(self):
        return self._outputs_obj is not None

    @property
    def output_schedule_ids(self):
        """Output schedule full IDs, primary first (empty if none)."""
        o = self._outputs_obj
        return [s["id"] for s in o["schedules"]] if o else []

    def output_schedule_pool_tags(self):
        """Map output schedule full ID -> its pool tag."""
        o = self._outputs_obj
        return {s["id"]: s["pool_tag"] for s in o["schedules"]} if o else {}

    @property
    def primary_output_schedule_id(self):
        oids = self.output_schedule_ids
        return oids[0] if oids else None

    @property
    def output_benchmark_set_ids(self):
        o = self._outputs_obj
        return list(o.get("benchmark_sets", [])) if o else []

    @property
    def output_schedule_id(self):
        """The primary output schedule (or None) -- the canonical "the session's
        output schedule" (session_output / terminus tools)."""
        return self.primary_output_schedule_id

    def set_outputs(self, schedule_pool_pairs, benchmark_set_ids):
        """Set the session outputs (self-closing it).  *schedule_pool_pairs* is
        an ordered list of (schedule full ID, pool tag); the first is primary."""
        if self.has_outputs():
            raise DhHlError("session already has outputs")
        self._outputs = {
            "schedules": [{"id": sid, "pool_tag": tag}
                          for sid, tag in schedule_pool_pairs],
            "benchmark_sets": list(benchmark_set_ids),
        }
        self._outputs_dirty = True
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
        return self.has_outputs() or self.delisted

    # -- flush -----------------------------------------------------------
    def flush(self):
        if self.is_new:
            safety.makedirs_tracked(self.dir)
            safety.new_file(os.path.join(self.dir, "prompt.txt"), self.prompt)
            safety.new_file(os.path.join(self.dir, "seed_ideas.json"),
                            json.dumps(self.seed_idea_ids, indent=1) + "\n")
            if self._parent_id is not None:
                safety.new_file(os.path.join(self.dir, "parent.txt"),
                                self._parent_id + "\n")
            if self._default_anchor is not None:
                safety.new_file(
                    os.path.join(self.dir, "default_anchor_schedule.txt"),
                    self._default_anchor + "\n")
        # outputs.json / delisted.txt are presence/pointer files added once and
        # never modified -- created whether the node is new or pre-existing
        # (close_session / delist on an existing session).
        if self._outputs_dirty and self._outputs is not None:
            safety.new_file(os.path.join(self.dir, "outputs.json"),
                            json.dumps(self._outputs, indent=1) + "\n")
        if self._delisted_dirty and self._delisted:
            safety.new_file(os.path.join(self.dir, "delisted.txt"), "")


# ---------------------------------------------------------------------------
# Current idea state
# ---------------------------------------------------------------------------

class CurrentIdeaState:
    """Parsed current_idea_state.txt.  Never raises on parse; competing/absent
    states are recorded and surfaced only when a caller needs a definite state.

    kind is an IdeaStateKind member (MISSING / NO_IDEA / IDEA / CONFLICT).
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
        if self.kind == IdeaStateKind.MISSING:
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
            self.kind = IdeaStateKind.MISSING
            return
        found = []  # list of (IdeaStateKind.NO_IDEA, ts) / (IdeaStateKind.IDEA, id)
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                v = _parse_state_line(line)
                if v is not None:
                    found.append(v)
        self.parsed_lines = [_encode_state(v) for v in found]
        if len(found) == 0:
            # file exists but nothing parsed = cruft
            self.kind = IdeaStateKind.CONFLICT
        elif len(found) == 1:
            v = found[0]
            if v[0] is IdeaStateKind.NO_IDEA:
                self.kind, self.timestamp = IdeaStateKind.NO_IDEA, v[1]
            else:
                self.kind, self.idea_id = IdeaStateKind.IDEA, v[1]
        else:
            self.kind = IdeaStateKind.CONFLICT

    def _require_catalog(self):
        if self.catalog is None:
            raise DhHlError(
                "cannot mutate current idea state without a locked catalog")

    def set_no_idea(self, timestamp):
        self._require_catalog()
        self.kind = IdeaStateKind.NO_IDEA
        self.timestamp, self.idea_id = timestamp, None
        self._dirty = True
        self.catalog._mark_dirty(self)

    def set_idea(self, idea_id):
        self._require_catalog()
        self.kind = IdeaStateKind.IDEA
        self.idea_id, self.timestamp = idea_id, None
        self._dirty = True
        self.catalog._mark_dirty(self)

    def encode(self):
        if self.kind is IdeaStateKind.NO_IDEA:
            return _encode_state((IdeaStateKind.NO_IDEA, self.timestamp))
        if self.kind is IdeaStateKind.IDEA:
            return _encode_state((IdeaStateKind.IDEA, self.idea_id))
        raise DhHlError("cannot encode current idea state of kind "
                        + str(self.kind))

    def flush(self):
        if self._dirty:
            # The private dir is guaranteed to exist by the time we flush: every
            # current-idea-state mutation goes through a tool that first calls
            # SessionWorkspace.ensure_private_dir() (and session-lock tools also
            # created it in locks.acquire_session).  new_file(overwrite_allowed=
            # True) picks an exclusive create or a deferred overwrite
            # automatically.
            safety.new_file(self.path, self.encode() + "\n",
                            overwrite_allowed=True)


def _parse_state_line(line):
    """Return (IdeaStateKind.NO_IDEA, ts) / (IdeaStateKind.IDEA, idea_id) / None
    (cruft).  The `dendritic_hl_root(...)`/`dendritic_hl_idea(...)` wrapper is the
    on-disk encoding (the project name avoids collisions, see impl.md); the
    IdeaStateKind member is the in-memory tag."""
    inner = _unwrap(line, "dendritic_hl_root")
    if inner is not None and ids.is_timestamp(inner):
        return (IdeaStateKind.NO_IDEA, inner)
    inner = _unwrap(line, "dendritic_hl_idea")
    if inner is not None and ids.looks_like_idea_id(inner):
        return (IdeaStateKind.IDEA, inner)
    return None


def _unwrap(line, name):
    prefix = name + "("
    if line.startswith(prefix) and line.endswith(")"):
        return line[len(prefix):-1]
    return None


def _encode_state(v):
    if v[0] is IdeaStateKind.NO_IDEA:
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
        self.benchmark_sets_dir = os.path.join(self.catalog_dir, "benchmark_sets")
        self.problem_dir = os.path.join(self.catalog_dir, "problem")
        self.private_dir = os.path.join(self.catalog_dir, "private")
        self._schedules = None
        self._ideas = None
        self._sessions = None
        self._benchmark_sets = None
        self._problems = None
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
                    if ids.looks_like_schedule_id(name):
                        self._schedules[name] = ScheduleNode(self, name)
        return self._schedules

    @property
    def ideas(self):
        if self._ideas is None:
            self._ideas = {}
            if os.path.isdir(self.idea_dir):
                for name in os.listdir(self.idea_dir):
                    if ids.looks_like_idea_id(name):
                        self._ideas[name] = IdeaNode(self, name)
        return self._ideas

    @property
    def sessions(self):
        if self._sessions is None:
            self._sessions = {}
            if os.path.isdir(self.session_dir):
                for name in os.listdir(self.session_dir):
                    if ids.looks_like_session_id(name):
                        self._sessions[name] = SessionNode(self, name)
        return self._sessions

    @property
    def benchmark_sets(self):
        if self._benchmark_sets is None:
            self._benchmark_sets = {}
            if os.path.isdir(self.benchmark_sets_dir):
                for name in os.listdir(self.benchmark_sets_dir):
                    if name.endswith(".json"):
                        stem = name[:-len(".json")]
                        if ids.looks_like_benchmark_set_id(stem):
                            self._benchmark_sets[stem] = BenchmarkSet(self, stem)
        return self._benchmark_sets

    def create_benchmark_set(self, data):
        """Create a new benchmark set holding *data* (the 3-level index).  The
        full ID is "{sanitized hostname}_{timestamp}", minted under the catalog
        lock like every other timestamped catalog name."""
        host = ids.sanitize_component(ids.stable_hostname())
        ts = self.mint_timestamped_name(
            lambda t: os.path.join(self.benchmark_sets_dir,
                                   ids.make_benchmark_set_id(host, t) + ".json"))
        full_id = ids.make_benchmark_set_id(host, ts)
        bs = BenchmarkSet(self, full_id, data=data, is_new=True)
        self.benchmark_sets[full_id] = bs
        return bs

    def get_benchmark_set(self, full_id):
        bs = self.benchmark_sets.get(full_id)
        if bs is None:
            raise DhHlError("no such benchmark set: " + full_id)
        return bs

    # -- problems --------------------------------------------------------
    @property
    def problems(self):
        if self._problems is None:
            self._problems = {}
            if os.path.isdir(self.problem_dir):
                for name in os.listdir(self.problem_dir):
                    if ids.looks_like_problem_id(name):
                        self._problems[name] = Problem(self, name)
        return self._problems

    def create_problem(self, argv, short_name, state=ProblemState.ENABLED):
        """Create a new problem from *argv* + *short_name* (default state
        ENABLED).  The full ID is the content hash of the canonical argv, so an
        identical argv is the same object: error (naming the existing problem) if
        it already exists (idea.md "New Problem Tool")."""
        validate_problem_argv(argv)
        if not ids.is_problem_short_name(short_name):
            raise DhHlError(
                "problem short name must be 1+ chars of [A-Za-z0-9_]: "
                + repr(short_name))
        assert isinstance(state, ProblemState)
        full_id = ids.sha256_hex(dump_problem_argv(argv))
        if full_id in self.problems:
            raise DhHlError("an identical problem already exists: "
                            + self.format_problem_id(self.problems[full_id]))
        p = Problem(self, full_id, is_new=True, argv=argv, state=state,
                    short_name=short_name)
        self.problems[full_id] = p
        return p

    def get_problem(self, full_id):
        p = self.problems.get(full_id)
        if p is None:
            raise DhHlError("no such problem: " + full_id)
        return p

    def enabled_problems(self):
        """All problems whose state is `enabled` or `main` (idea.md)."""
        return [p for p in self.problems.values() if p.is_enabled()]

    def select_problems(self, problem_args):
        """The problems named by *problem_args* (deduped, in order), or all
        enabled problems if none were named -- the shared `--problem` selection
        for `build` and the cost tools (idea.md)."""
        if not problem_args:
            return self.enabled_problems()
        out, seen = [], set()
        for spec in problem_args:
            problem = self.resolve_problem(spec)
            if problem.full_id not in seen:
                seen.add(problem.full_id)
                out.append(problem)
        return out

    def main_problem(self):
        """The unique problem with `main` state; error if not well-defined."""
        mains = [p for p in self.problems.values()
                 if p.state == ProblemState.MAIN]
        if len(mains) == 1:
            return mains[0]
        if not mains:
            raise DhHlError(
                "no main problem is set (use `dh_hl set_main_problem`)")
        raise DhHlError(
            "multiple problems have `main` state (catalog is inconsistent):\n"
            + "\n".join("  " + p.full_id for p in mains))

    def resolve_problem(self, s):
        return _resolve_problem(self, s)

    def format_problem_id(self, p):
        return _format_problem_short(self, p)

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

    # -- warning-toggle resolution --------------------------------------
    def schedule_path_to_root(self, schedule):
        """The schedule nodes on the path from *schedule* up to (and including)
        its tree root, nearest first."""
        path = []
        node = schedule
        seen = set()
        while node is not None and node.full_id not in seen:
            seen.add(node.full_id)
            path.append(node)
            if node.is_root():
                break
            idea = node.parent_idea()
            node = idea.parent_schedule() if idea is not None else None
        return path

    def warning_toggle_state(self, schedule):
        """Resolve the WarningToggle block algorithm (idea.md "WarningToggle
        State") for *schedule*.  Returns `(toggles, cancelled_ids)` where
        `toggles` are all WarningToggle objects owned by nodes on the
        node-to-root path and `cancelled_ids` is the set of full IDs among them
        cancelled by some toggle in that same set."""
        toggles = []
        for node in self.schedule_path_to_root(schedule):
            toggles.extend(node.warning_toggles)
        present = {w.full_id for w in toggles}
        cancelled_ids = set()
        for w in toggles:
            if w.cancels is not None and w.cancels in present:
                cancelled_ids.add(w.cancels)
        return toggles, cancelled_ids

    def blocking_toggle(self, schedule, rule, func):
        """A surviving (non-cancelled) WarningToggle on *schedule*'s node-to-root
        path that blocks the (rule, func) warning, or None.  Picks arbitrarily if
        several qualify (idea.md `view_benchmark_warnings`)."""
        toggles, cancelled_ids = self.warning_toggle_state(schedule)
        for w in toggles:
            if w.full_id in cancelled_ids:
                continue
            if w.is_block() and w.rule == rule and w.func == func:
                return w
        return None

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
        policy (idea.md), the walk must not loop on a cooked catalog: every step
        up a sub-session edge must go to a strictly-older session (the session
        timestamp invariant).  Rather than silently absorb a violation (which
        would invite off-label reliance on the walk's behavior over corrupt
        state), we RAISE on it -- this both terminates the walk and surfaces the
        corruption.  Since each valid step is strictly older, the walk visits
        each session at most once and always terminates."""
        current = session
        while True:
            if current.is_self_closed():
                return True
            pid = current.parent_id
            if pid is None or pid not in self.sessions:
                return False
            parent = self.sessions[pid]
            if parent.depth != current.depth - 1:
                return False  # successor edge (legit) or non-sub edge: don't walk
            if not (parent.timestamp < current.timestamp):
                raise DhHlError(
                    "tree invariant violation: session {} is not older than its "
                    "sub-session {} (cooked catalog)".format(
                        parent.full_id, current.full_id))
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

    def create_schedule(self, source, parent_idea=None, params_text=None):
        """Create a brand-new schedule node holding *source* (a str) and
        *params_text* (the generator_parameters.json text; defaults to the
        canonical "[{}]" -- benchmark once with no parameters).  If parent_idea
        is given, link it (checking invariants); else it's a root.

        The node's content hash covers BOTH files (idea.md "Hash Format"), so
        two schedules differing only in generator parameters are distinct nodes."""
        if params_text is None:
            params_text = dump_parameters(DEFAULT_PARAMETERS)
        h = ids.schedule_content_hash(source, params_text)
        ts = self.mint_timestamped_name(
            lambda t: os.path.join(self.sch_dir, ids.make_schedule_id(t, h)))
        full_id = ids.make_schedule_id(ts, h)
        node = ScheduleNode(self, full_id, is_new=True, source=source,
                            params_text=params_text)
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
        hostname = ids.stable_hostname()  # sanitized inside make_session_id
        ts = self.mint_timestamped_name(
            lambda t: os.path.join(
                self.session_dir,
                ids.make_session_id(depth, t, username, hostname)))
        return ids.make_session_id(depth, ts, username, hostname)

    def create_session(self, seed_ideas, parent_session, depth, *, prompt="",
                       default_anchor_schedule_id=None, session_id=None):
        """Create a new session node seeded with *seed_ideas* at *depth* (0 for
        top-level).  Model-level primitive; the CLI session-creation tools wrap
        this and also initialize the private workspace.  *session_id* may be a
        pre-minted ID (mint_session_id); otherwise one is minted here.

        *seed_ideas* is a single IdeaNode or a list of IdeaNodes/full-ID strings
        (>= 1).  *prompt* is the session prompt text.  *default_anchor_schedule_id*
        is an optional schedule full ID.

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
        if not isinstance(seed_ideas, (list, tuple)):
            seed_ideas = [seed_ideas]
        seed_ids = [s.full_id if hasattr(s, "full_id") else s for s in seed_ideas]
        node = SessionNode(self, session_id, is_new=True, seed_idea_ids=seed_ids,
                           prompt=prompt, parent_id=parent_id,
                           default_anchor_schedule_id=default_anchor_schedule_id)
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
        safety.makedirs_tracked(self.benchmark_sets_dir)
        safety.makedirs_tracked(self.problem_dir)
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

    def format_commentary_id(self, c):
        return _format_commentary_short(self, c)

    def format_warning_toggle_id(self, w):
        return _format_warning_toggle_short(self, w)

    def format_benchmark_id(self, b):
        return _format_benchmark_short(self, b)

    # -- ID resolution ---------------------------------------------------
    def resolve_schedule(self, s):
        return _resolve_schedule(self, s)

    def resolve_idea(self, s):
        return _resolve_idea(self, s)

    def resolve_commentary(self, s):
        return _resolve_commentary(self, s)

    def resolve_warning_toggle(self, s):
        return _resolve_warning_toggle(self, s)

    def resolve_benchmark(self, s):
        return _resolve_benchmark(self, s)

    def resolve_benchmark_set(self, s):
        """Benchmark sets have no short-ID form (idea.md), so resolution is an
        exact full-ID match against benchmark_sets/."""
        if not ids.looks_like_benchmark_set_id(s):
            raise DhHlError("not a valid benchmark set ID: " + s)
        return self.get_benchmark_set(s)


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
    if ids.looks_like_idea_id(s):
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
    if ids.looks_like_schedule_id(s):
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
    if ids.looks_like_idea_id(idea_part):
        node = catalog.ideas.get(idea_part)
        return [node] if node is not None else []
    hp, dot, pp = idea_part.partition(".")
    if dot == "" or "." in pp:
        return []
    return _match_ideas(catalog, hp, pp)


def _resolve_schedule_matches_lenient(catalog, s):
    """Like _resolve_schedule but returns *all* matches (no single-match /
    empty-match error); accepts a full or short schedule ID."""
    if ids.looks_like_schedule_id(s):
        node = catalog.schedules.get(s)
        return [node] if node is not None else []
    try:
        return _match_schedules(catalog, s)
    except DhHlError:
        return []


def _is_commentary_full_id(s):
    # Commentary full ID = "{schedule full id}_{ts}_{hash}".  The trailing
    # "{ts}_{hash}" (the local ID) has the exact shape of a schedule full ID, so
    # both halves check with looks_like_schedule_id.  Total = 90 + 1 + 90 = 181.
    if len(s) != ids.SCHEDULE_ID_LEN * 2 + 1:
        return False
    sched = s[:ids.SCHEDULE_ID_LEN]
    sep = s[ids.SCHEDULE_ID_LEN]
    local = s[ids.SCHEDULE_ID_LEN + 1:]
    return sep == "_" and ids.looks_like_schedule_id(sched) and ids.looks_like_schedule_id(local)


def _resolve_commentary(catalog, s):
    # Full commentary ID?
    if _is_commentary_full_id(s):
        sched_id = s[:ids.SCHEDULE_ID_LEN]
        local = s[ids.SCHEDULE_ID_LEN + 1:]
        node = catalog.schedules.get(sched_id)
        if node is not None:
            for c in node.commentary:
                if c.local_id == local:
                    return c
        raise DhHlError("no such commentary: " + s)
    # Short form: {schedule ID}.{comment hash prefix}.  The schedule ID may
    # itself be a short ID containing '.', so split off the LAST component.
    sched_part, dot, hp = s.rpartition(".")
    if dot == "" or not _is_hex(hp):
        raise DhHlError("not a valid commentary ID: " + repr(s))
    matches = []
    for node in _resolve_schedule_matches_lenient(catalog, sched_part):
        for c in node.commentary:
            if c.hash.startswith(hp):
                matches.append(c)
    # dedupe by full_id
    seen, uniq = set(), []
    for c in matches:
        if c.full_id not in seen:
            seen.add(c.full_id)
            uniq.append(c)
    if len(uniq) == 1:
        return uniq[0]
    if not uniq:
        raise DhHlError("no commentary matches short ID: " + repr(s))
    ordered = sorted(uniq, key=lambda c: c.full_id)
    raise DhHlError("\n".join(
        ["ambiguous commentary ID; matches:"] + ["  " + c.full_id for c in ordered]))


def _resolve_warning_toggle(catalog, s):
    # Full ID?  "{schedule full id}_{timestamp}"
    if ids.looks_like_warning_toggle_id(s):
        sched_id = ids.warning_toggle_schedule_id(s)
        ts = ids.warning_toggle_timestamp(s)
        node = catalog.schedules.get(sched_id)
        if node is not None:
            for w in node.warning_toggles:
                if w.timestamp == ts:
                    return w
        raise DhHlError("no such WarningToggle: " + s)
    # Short form: {schedule ID}.{timestamp}.  The schedule ID may itself be a
    # short ID containing '.', so split off the LAST component.
    sched_part, dot, ts = s.rpartition(".")
    if dot == "" or not ids.is_timestamp(ts):
        raise DhHlError("not a valid WarningToggle ID: " + repr(s))
    matches = []
    for node in _resolve_schedule_matches_lenient(catalog, sched_part):
        for w in node.warning_toggles:
            if w.timestamp == ts:
                matches.append(w)
    seen, uniq = set(), []
    for w in matches:
        if w.full_id not in seen:
            seen.add(w.full_id)
            uniq.append(w)
    if len(uniq) == 1:
        return uniq[0]
    if not uniq:
        raise DhHlError("no WarningToggle matches short ID: " + repr(s))
    ordered = sorted(uniq, key=lambda w: w.full_id)
    raise DhHlError("\n".join(
        ["ambiguous WarningToggle ID; matches:"] + ["  " + w.full_id for w in ordered]))


def _resolve_benchmark(catalog, s):
    # Full ID?  "{schedule full id}_{hostname}_{timestamp}"
    if ids.looks_like_benchmark_id(s):
        sched_id = ids.benchmark_schedule_id(s)
        local = ids.benchmark_local_part(s)
        node = catalog.schedules.get(sched_id)
        if node is not None:
            for b in node.benchmarks:
                if b.local_id == local:
                    return b
        raise DhHlError("no such benchmark: " + s)
    # Short form: {schedule ID}.{hostname}_{timestamp}.  The schedule ID may
    # itself be a short ID containing '.'; the "{hostname}_{ts}" tail has none.
    sched_part, dot, local = s.rpartition(".")
    if dot == "" or not ids.looks_like_benchmark_local_id(local):
        raise DhHlError("not a valid benchmark ID: " + repr(s))
    matches = []
    for node in _resolve_schedule_matches_lenient(catalog, sched_part):
        for b in node.benchmarks:
            if b.local_id == local:
                matches.append(b)
    seen, uniq = set(), []
    for b in matches:
        if b.full_id not in seen:
            seen.add(b.full_id)
            uniq.append(b)
    if len(uniq) == 1:
        return uniq[0]
    if not uniq:
        raise DhHlError("no benchmark matches short ID: " + repr(s))
    ordered = sorted(uniq, key=lambda b: b.full_id)
    raise DhHlError("\n".join(
        ["ambiguous benchmark ID; matches:"] + ["  " + b.full_id for b in ordered]))


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
    if "." not in idea_short and not ids.looks_like_idea_id(idea_short):
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


def _format_commentary_short(catalog, c):
    """A short commentary ID "{schedule short id}.{comment hash prefix}",
    falling back to the full ID if none is unambiguous."""
    sched_short = _format_schedule_short(catalog, c.schedule)
    h = c.hash
    for hlen in range(_MIN_HASH, len(h) + 1):
        cand = "{}.{}".format(sched_short, h[:hlen])
        try:
            if catalog.resolve_commentary(cand).full_id == c.full_id:
                return cand
        except DhHlError:
            pass
    return c.full_id


def _format_warning_toggle_short(catalog, w):
    """A short WarningToggle ID "{schedule short id}.{timestamp}", falling back to
    the full ID if that is somehow ambiguous."""
    sched_short = _format_schedule_short(catalog, w.schedule)
    cand = "{}.{}".format(sched_short, w.timestamp)
    try:
        if catalog.resolve_warning_toggle(cand).full_id == w.full_id:
            return cand
    except DhHlError:
        pass
    return w.full_id


def _format_benchmark_short(catalog, b):
    """A short benchmark ID "{schedule short id}.{hostname}_{timestamp}", falling
    back to the full ID if that is somehow ambiguous."""
    sched_short = _format_schedule_short(catalog, b.schedule)
    cand = "{}.{}".format(sched_short, b.local_id)
    try:
        if catalog.resolve_benchmark(cand).full_id == b.full_id:
            return cand
    except DhHlError:
        pass
    return b.full_id


def _resolve_problem(catalog, s):
    # `main`: the (unique) main problem.  Accepted, never generated (idea.md).
    # The reserved short-spec token is exactly the main state's wire value.
    if s == ProblemState.MAIN.value:
        return catalog.main_problem()
    # Full ID = the 64-hex content hash.
    if ids.looks_like_problem_id(s):
        return catalog.get_problem(s)
    # Short form: problem.{short name}, matching ENABLED problems only (idea.md).
    if s.startswith("problem."):
        name = s[len("problem."):]
        matches = [p for p in catalog.enabled_problems() if p.short_name == name]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise DhHlError("no enabled problem matches short ID: " + repr(s))
        ordered = sorted(matches, key=lambda p: p.full_id)
        raise DhHlError("\n".join(
            ["ambiguous problem ID; matches:"]
            + ["  " + p.full_id for p in ordered]))
    raise DhHlError("not a valid problem ID: " + repr(s))


def _format_problem_short(catalog, p):
    """A short problem ID "problem.{short name}" (only for enabled problems, and
    only if it resolves unambiguously to *p*), else the full ID."""
    if p.is_enabled():
        cand = "problem." + p.short_name
        try:
            if catalog.resolve_problem(cand).full_id == p.full_id:
                return cand
        except DhHlError:
            pass
    return p.full_id
