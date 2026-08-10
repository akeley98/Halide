"""Per-tool execution context: -C/-s resolution, catalog lock, session workspace.

Replaces the old workspace-file-anchored Context.  A tool now identifies its
target with -C (catalog directory) and/or -s (session handle or full ID); the
workspace C++ file, current idea state, and bin/ live inside the session's
gitignored private workspace (private/{session_id}/), not as an external file.

Lock policy (see impl.md "Tool Safety: Lock Hierarchy"): the machine lock is
already held (main()).  Context.for_session optionally takes the session lock,
then Context acquires the catalog lock.  build/profile use the raw constructor
so they can compile before locking the catalog (and, for profile, upgrade the
machine lock to exclusive first).
"""

import json
import os
import sys
from types import MappingProxyType

from . import ids
from . import locks
from . import safety
from .catalog import (Catalog, CurrentIdeaState, DEFAULT_PARAMETERS,
                      dump_parameters, _UNLOADED)
from .errors import DhHlError


class _PrivateMapState:
    """A JSON-object private-workspace file modelled with the catalog object
    discipline (impl.md "Tool Internal Design"): lazy-load the mapping ONCE into
    memory, mutate in memory, flush ONCE -- no bare per-call disk reads.

    Mutation requires a (locked) catalog: the object registers on the catalog's
    dirty set so `catalog.flush()` calls this object's `flush()`, exactly like
    `CurrentIdeaState`.  Reading needs no catalog (build reads lock-free).  A
    single in-memory `map` is why repeated mutations in a loop (e.g. join_session
    adding several benchmark sets) all persist -- the pre-object code re-read the
    file each call and only the last write survived."""

    def __init__(self, path, catalog=None):
        self._path = path
        self._catalog = catalog
        # _UNLOADED until first access; an absent file *loads* as {} (a missing
        # list is an empty list).  This is a load default only -- flush writes
        # solely when _dirty (i.e. after a real mutation), so a pure read never
        # creates the file.
        self._map = _UNLOADED
        self._dirty = False

    @property
    def path(self):
        return self._path

    def _loaded(self):
        """Lazy-load the backing dict once (absent file -> {}).  Deliberately a
        method, not a public `map` property: it is the *mutable* store, so only
        this class's own methods touch it -- reads through it, writes ONLY via
        `_set_item`/`_remove_item` (which dirty).  External readers use `view`.
        It can't dirty on its own access because it is also the read path."""
        if self._map is _UNLOADED:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._map = json.load(f)
            except FileNotFoundError:
                self._map = {}
        return self._map

    @property
    def view(self):
        """A read-only view of the mapping for external readers.  Mutating it
        raises TypeError (Python's nearest thing to a const reference), so state
        can only change through the mutation primitives below."""
        return MappingProxyType(self._loaded())

    # Mutation primitives: the ONLY way to change the mapping, so a change can
    # never skip the dirty registration (mutation and dirtying are inseparable).
    def _set_item(self, key, value, catalog=None):
        self._loaded()[key] = value
        self._mark_dirty(catalog)

    def _remove_item(self, key):
        """Delete *key* if present; returns whether it was.  Dirties iff it
        actually removed something."""
        m = self._loaded()
        if key not in m:
            return False
        del m[key]
        self._mark_dirty()
        return True

    def _mark_dirty(self, catalog=None):
        catalog = catalog or self._catalog
        if catalog is None:
            raise DhHlError(
                "cannot mutate private workspace state without a locked catalog")
        self._catalog = catalog
        self._dirty = True
        catalog._mark_dirty(self)

    def flush(self):
        # The private dir is guaranteed to exist by flush time (mutating tools
        # call ensure_private_dir / the session lock created it), matching the
        # CurrentIdeaState assumption.
        if self._dirty:
            safety.write_allowed(self._path,
                                 json.dumps(self._map, indent=1) + "\n")


class PrivateIdeaList(_PrivateMapState):
    """The session private idea list: ``{idea full ID -> pool tag}``."""

    def contains(self, idea_id):
        return idea_id in self._loaded()

    def get(self, idea_id):
        try:
            return self._loaded()[idea_id]
        except KeyError:
            raise DhHlError(
                "idea is not in the session's private idea list: " + idea_id)

    def ids(self):
        return list(self._loaded())

    def set(self, idea_id, pool_tag):
        self._set_item(idea_id, pool_tag)

    def remove(self, idea_id):
        if not self._remove_item(idea_id):  # idea.md close_session note
            raise DhHlError(
                "idea is not in the session's private idea list: " + idea_id)

    def rename(self, before, after):
        # Scan (read) for matches, then rewrite each through the primitive.
        matches = [k for k, v in self._loaded().items() if v == before]
        for k in matches:
            self._set_item(k, after)
        return len(matches)


class PrivateBenchmarkSetList(_PrivateMapState):
    """The session private benchmark set list: ``{benchmark set full ID ->
    cached cost stats}`` (the cache is built by `_benchmark_set_cache`)."""

    def ids(self):
        return list(self._loaded())

    def add(self, set_id, catalog):
        # Caching reads the referenced benchmark sub-objects, hence a catalog.
        self._set_item(set_id, _benchmark_set_cache(catalog, set_id), catalog)

    def remove(self, set_id):
        self._remove_item(set_id)  # silent no-op if absent


class CurrentAnchor:
    """current_anchor_schedule.txt: a single schedule full ID, or None (empty or
    absent file).  Lazy-load once + flush, per the object discipline."""

    def __init__(self, path, catalog=None):
        self._path = path
        self._catalog = catalog
        self._value = _UNLOADED     # str id / None (no anchor) once loaded
        self._dirty = False

    @property
    def path(self):
        return self._path

    @property
    def value(self):
        if self._value is _UNLOADED:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._value = f.read().strip() or None
            except FileNotFoundError:
                self._value = None
        return self._value

    def set(self, schedule_id):
        if self._catalog is None:
            raise DhHlError(
                "cannot mutate the current anchor without a locked catalog")
        self._value = schedule_id or None
        self._dirty = True
        self._catalog._mark_dirty(self)

    def flush(self):
        if self._dirty:
            safety.write_allowed(
                self._path, (self._value + "\n") if self._value else "")


class SessionWorkspace:
    """The gitignored private workspace of one session: generator.cpp,
    current_idea_state.txt, bin/.  All paths are lock-free; mutating callers are
    responsible for holding the session lock (not enforced here, per idea.md)."""

    def __init__(self, catalog_dir, session_id, catalog=None):
        # catalog_dir is enough for every read + the compile paths (lock-free).
        # A catalog is required only to *mutate* the current idea state, which
        # happens under the catalog lock -- so build's pre-lock compile can use a
        # catalog-free workspace, honoring the "a Catalog means the lock is held"
        # invariant.
        self.catalog = catalog
        self.session_id = session_id
        self.catalog_dir = os.path.abspath(catalog_dir)
        self.private_dir = os.path.join(self.catalog_dir, "private", session_id)
        self.workspace_path = os.path.join(self.private_dir, "generator.cpp")
        self.params_path = os.path.join(self.private_dir,
                                        "generator_parameters.json")
        self.bin_dir = os.path.join(self.private_dir, "bin")
        self._workspace_bytes = None
        self._params_text = None
        self._current_idea = None
        self._idea_list = None            # PrivateIdeaList
        self._benchmark_set_list = None   # PrivateBenchmarkSetList
        self._anchor = None               # CurrentAnchor

    @property
    def current_idea_state(self):
        if self._current_idea is None:
            self._current_idea = CurrentIdeaState(self.private_dir, self.catalog)
        return self._current_idea

    # The three private-workspace state objects (lazy-created once; they lazy-load
    # their file once and flush via the catalog dirty set -- see the classes
    # above and impl.md "Tool Internal Design").
    @property
    def idea_list(self):
        if self._idea_list is None:
            self._idea_list = PrivateIdeaList(self.private_ideas_path,
                                              self.catalog)
        return self._idea_list

    @property
    def benchmark_set_list(self):
        if self._benchmark_set_list is None:
            self._benchmark_set_list = PrivateBenchmarkSetList(
                self.private_benchmark_sets_path, self.catalog)
        return self._benchmark_set_list

    @property
    def anchor(self):
        if self._anchor is None:
            self._anchor = CurrentAnchor(self.current_anchor_path, self.catalog)
        return self._anchor

    def ensure_private_dir(self):
        """Create private/ and private/{id} (gitignored -> absent on a fresh
        clone).  This is the single place the private-workspace dir is created
        implicitly; every path that WRITES the workspace (initialize, restore,
        new_root/set_idea's idea-state, build's bin/) calls it first.  The
        catalog-directory existence check prevents a typo'd catalog dir from
        silently creating a chain of unwanted directories."""
        if not os.path.isdir(self.catalog_dir):
            raise DhHlError(
                "no catalog directory: " + self.catalog_dir
                + " (refusing to create a private workspace under it)")
        safety.makedirs_tracked(self.private_dir)

    # The two "workspace files" (idea.md terminology): the only workspace state
    # the harness user edits directly.  Both are required -- there is no implicit
    # default for the parameters file (use `init_workspace` to create them).
    _WORKSPACE_FILES = ("generator.cpp", "generator_parameters.json")

    def missing_workspace_files(self):
        """Which of the two workspace files are absent (in a stable order)."""
        missing = []
        if not os.path.isfile(self.workspace_path):
            missing.append("generator.cpp")
        if not os.path.isfile(self.params_path):
            missing.append("generator_parameters.json")
        return missing

    def has_workspace_files(self):
        return not self.missing_workspace_files()

    def require_workspace_files(self):
        if not self.has_workspace_files():
            raise DhHlError(_missing_workspace_message(
                self.missing_workspace_files(), self.private_dir))

    @property
    def workspace_bytes(self):
        if self._workspace_bytes is None:
            self._workspace_bytes = self._read_workspace_file(
                self.workspace_path, "generator.cpp")
        return self._workspace_bytes

    @property
    def workspace_source(self):
        return self.workspace_bytes.decode("utf-8")

    @property
    def workspace_params_text(self):
        """Text of the workspace generator_parameters.json (required; no implicit
        default -- a missing file is a clean 'run init_workspace' error)."""
        if self._params_text is None:
            self._params_text = self._read_workspace_file(
                self.params_path, "generator_parameters.json").decode("utf-8")
        return self._params_text

    def _read_workspace_file(self, path, label):
        """Read a workspace file as bytes, turning a missing file into a friendly
        'run init_workspace' error (naming the path) instead of a raw traceback
        (idea.md init_workspace notes)."""
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            raise DhHlError(_missing_workspace_message([label], self.private_dir))

    # -- current anchor schedule ----------------------------------------
    @property
    def current_anchor_path(self):
        return os.path.join(self.private_dir, "current_anchor_schedule.txt")

    @property
    def current_anchor_schedule_id(self):
        """The current anchor schedule full ID, or None (absent or empty file)."""
        return self.anchor.value

    def set_current_anchor(self, schedule_id):
        """Set (or clear, when *schedule_id* is None) the current anchor.  An
        empty file encodes 'no anchor' (we never delete files).  `init_workspace`
        writes the file directly (with its --force `allow` flag), so this
        deferred-flush path is only the ordinary set_current_anchor tool."""
        self.ensure_private_dir()
        self.anchor.set(schedule_id)

    @property
    def workspace_hash(self):
        # Content hash covers BOTH workspace files, identical to how a schedule
        # node's ID hash is computed (idea.md "Hash Format"), so `status` /
        # `new_root` node matching stays exact.
        return ids.schedule_content_hash(self.workspace_bytes,
                                         self.workspace_params_text)

    def initialize(self, source, idea_state, params_text=None):
        """Initialize a fresh private workspace: write generator.cpp and
        generator_parameters.json (deferred overwrites, never rolled back --
        honoring 'never delete the workspace file') and set the current idea
        state.  *idea_state* is ('idea', id) or ('no_idea', timestamp).
        *params_text* defaults to the canonical "[{}]"."""
        self.ensure_private_dir()
        if params_text is None:
            params_text = dump_parameters(DEFAULT_PARAMETERS)
        safety.queue_overwrite(self.workspace_path, source)
        safety.queue_overwrite(self.params_path, params_text)
        self._workspace_bytes = (source.encode("utf-8")
                                 if isinstance(source, str) else source)
        self._params_text = params_text
        kind, val = idea_state
        if kind == "idea":
            self.current_idea_state.set_idea(val)
        else:
            self.current_idea_state.set_no_idea(val)

    # -- private idea list ----------------------------------------------
    # private/{id}/private_ideas.json: {idea full ID -> pool tag}.  Unordered
    # (the cost-ranked view sorts at read time); cost is NOT stored here, it is
    # derived when needed.  Backed by the PrivateIdeaList object (self.idea_list);
    # these are thin delegations, kept for the existing call sites.
    @property
    def private_ideas_path(self):
        return os.path.join(self.private_dir, "private_ideas.json")

    def read_private_ideas(self):
        """A read-only view of the private idea list ({idea full ID -> pool
        tag}); mutate via the pool-tag methods.  It reflects live in-memory
        changes, so snapshot with dict() for a pre-mutation copy (see
        join_session)."""
        return self.idea_list.view

    def has_private_idea(self, idea_id):
        return self.idea_list.contains(idea_id)

    def get_pool_tag(self, idea_id):
        """The pool tag of *idea_id*; error if it isn't in the private list."""
        return self.idea_list.get(idea_id)

    def set_pool_tag(self, idea_id, pool_tag):
        """Set *idea_id*'s pool tag, adding it to the list if necessary."""
        self.ensure_private_dir()
        self.idea_list.set(idea_id, pool_tag)

    def hide_private_idea(self, idea_id):
        """Prepend a '.' to the idea's pool tag; error if not in the list."""
        self.set_pool_tag(idea_id, "." + self.get_pool_tag(idea_id))

    def rename_pool_tag(self, before, after):
        """Retag every idea whose pool tag is *before* to *after*.  Returns the
        count updated."""
        self.ensure_private_dir()
        return self.idea_list.rename(before, after)

    def remove_private_idea(self, idea_id):
        """Remove *idea_id* from the private idea list, erroring if absent.
        Retained as an internal helper (idea.md close_session note)."""
        self.ensure_private_dir()
        self.idea_list.remove(idea_id)

    # -- private benchmark set list -------------------------------------
    # private/{id}/private_benchmark_sets.json: {benchmark set full ID -> cached
    # cost stats} (see impl.md "Private Benchmark Sets on Disk" and
    # `_benchmark_set_cache`).  Backed by the PrivateBenchmarkSetList object.
    @property
    def private_benchmark_sets_path(self):
        return os.path.join(self.private_dir, "private_benchmark_sets.json")

    def read_private_benchmark_sets(self):
        """A read-only view of the private benchmark set list ({set full ID ->
        cache}); reflects live in-memory changes."""
        return self.benchmark_set_list.view

    def add_private_benchmark_set(self, set_id, catalog=None):
        """Add *set_id* to the private benchmark set list, caching its
        cost-relevant statistics (impl.md "Private Benchmark Sets on Disk").

        A *catalog* is required to read the referenced benchmark sub-objects; it
        defaults to ``self.catalog`` (set for tool-driven callers).  Callers hold
        the catalog lock (build's profile phase, join_session, the
        add_private_benchmark_set tool), so reading the benchmark files is safe.
        Re-adding, or adding several in a loop, is safe: the in-memory list
        accumulates and flushes once."""
        catalog = catalog or self.catalog
        if catalog is None:
            raise DhHlError(
                "add_private_benchmark_set needs a catalog to cache cost stats")
        self.ensure_private_dir()
        self.benchmark_set_list.add(set_id, catalog)

    def remove_private_benchmark_set(self, set_id):
        """Drop *set_id* from the private benchmark set list.  Silent no-op if it
        is not present (idea.md "Add/Remove Private Benchmark Set Tools")."""
        self.ensure_private_dir()
        self.benchmark_set_list.remove(set_id)


def _benchmark_set_cache(catalog, set_id):
    """Build the cached cost statistics stored for one private benchmark set
    (impl.md "Private Benchmark Sets on Disk").

    Shape::

        {"hostname": str, "cpu_count": num, "profiler_version": num,
         "schedules": {<schedule full id>: [<cell>, ... per params index]}}

    where each ``<cell>`` is ``{"wall_time_min": [...], "id": [...]}`` with both
    lists of length batch-count (parallel: the raw cost and the benchmark full ID
    for each batch).  The three top-level provenance fields must be identical
    across every benchmark in the set (a single profiling machine/run) -- we
    assert that.  ``profiler_version`` is carried verbatim; the cost core skips a
    set whose version isn't `catalog.EXPECTED_PROFILER_VERSION`, so a stale
    version is recorded here rather than rejected at add time."""
    bs = catalog.get_benchmark_set(set_id)
    prov = {}  # first-seen (host, cpu, ver, problem), asserted uniform
    schedules = {}
    for sched_id, per_pidx in bs.data.items():
        cells = []
        for batch_ids in per_pidx:  # one list per parameters index
            wall_time_min, ids_out = [], []
            for bid in batch_ids:
                bench = catalog.resolve_benchmark(bid)
                ids_out.append(bid)
                wall_time_min.append(bench.wall_time_min)
                # A set is single-problem (build makes one set per problem), so
                # the problem is uniform too -- asserted alongside the machine
                # provenance (idea.md "Cost Model", impl.md "Private Benchmark
                # Sets on Disk").
                seen = (bench.hostname, bench.cpu_count, bench.profiler_version,
                        bench.problem)
                first = prov.setdefault("provenance", seen)
                assert first == seen, (
                    "benchmark set {} mixes provenance {} vs {}".format(
                        set_id, first, seen))
            cells.append({"wall_time_min": wall_time_min, "id": ids_out})
        schedules[sched_id] = cells
    host, cpu, ver, problem = prov.get("provenance", (None, None, None, None))
    return {"hostname": host, "cpu_count": cpu, "profiler_version": ver,
            "problem": problem, "schedules": schedules}


def _missing_workspace_message(missing, private_dir):
    """A friendly 'run init_workspace' message naming the missing workspace
    file(s) and their directory (idea.md Status / init_workspace notes)."""
    return (
        "missing workspace {} in {}\n"
        "AGENTS: run `dh_hl init_workspace` to get files to edit".format(
            " and ".join(missing), private_dir))


def read_text_or_stdin(path):
    """Read a text input argument.  Universally, "-" means read from stdin;
    otherwise *path* is a filename.  A missing/unreadable file is a clean
    user-facing error, not a traceback."""
    if path == "-":
        return sys.stdin.read()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise DhHlError("cannot read input file {!r}: {}".format(path, e))


def _validate_catalog_dir(path):
    if not path.endswith(".dh_hl"):
        raise DhHlError("catalog directory must end with .dh_hl: " + path)
    return os.path.abspath(path)


def resolve_target(args):
    """Resolve -C/-s into (catalog_dir_abspath, session_id | None).  Lock-free.

    -s may be a session handle (``tmp....``) or a session full ID.  A handle
    substitutes for a mandatory -C; if both are given they must agree."""
    C = getattr(args, "catalog", None)
    s = getattr(args, "session", None)
    if s is not None and s.startswith("tmp."):
        cat_from_handle, session_id = locks.resolve_handle(s)
        if C is not None:
            C_abs = _validate_catalog_dir(C)
            if C_abs != os.path.abspath(cat_from_handle):
                raise DhHlError(
                    "-C {} does not match the catalog of session handle {} ({})"
                    .format(C_abs, s, cat_from_handle))
            return C_abs, session_id
        return os.path.abspath(cat_from_handle), session_id
    if s is not None:
        if not ids.is_session_id(s):
            raise DhHlError("not a session handle or valid session ID: " + s)
        if C is None:
            raise DhHlError(
                "-C is required when -s is a session full ID (not a handle)")
        return _validate_catalog_dir(C), s
    if C is None:
        raise DhHlError("a catalog (-C) or session (-s) is required")
    return _validate_catalog_dir(C), None


class Context:
    """Binds a Catalog to an optional current session + its private workspace.

    The raw constructor does NOT acquire any lock (build/profile rely on this to
    lock the catalog late).  Use the for_catalog / for_session factories for the
    normal acquire-then-work flow."""

    def __init__(self, catalog, session_id):
        # Takes an already-constructed (hence already-locked) Catalog; the
        # factories below acquire the lock before constructing it.
        self.catalog = catalog
        self.session_id = session_id
        self._session = None
        self._workspace = None

    # -- lock acquisition + construction --------------------------------
    @staticmethod
    def _open_catalog(catalog_dir):
        """Acquire the catalog lock, then construct the Catalog (whose __init__
        asserts the lock is held for it)."""
        if not os.path.isdir(catalog_dir):
            raise DhHlError("no catalog directory: " + catalog_dir)
        locks.acquire_catalog(catalog_dir)
        return Catalog(catalog_dir)

    @classmethod
    def for_catalog(cls, args):
        """A -C tool: requires the catalog; accepts -s only to default the
        [schedule ID] argument.  Takes the catalog lock, not the session lock."""
        catalog_dir, session_id = resolve_target(args)
        return cls(cls._open_catalog(catalog_dir), session_id)

    @classmethod
    def for_session(cls, args, *, session_lock):
        """A -s tool: requires a session.  Optionally takes the (exclusive,
        non-blocking) session lock, then the catalog lock."""
        catalog_dir, session_id = resolve_target(args)
        if session_id is None:
            raise DhHlError("this command requires a session (-s)")
        # Guard before acquire_session, which would otherwise create private/{id}
        # (and the whole chain) under a typo'd catalog dir.  _open_catalog
        # re-checks, but only after the session lock is taken.
        if not os.path.isdir(catalog_dir):
            raise DhHlError("no catalog directory: " + catalog_dir)
        if session_lock:
            locks.acquire_session(catalog_dir, session_id)
        return cls(cls._open_catalog(catalog_dir), session_id)

    # -- session + workspace --------------------------------------------
    @property
    def session(self):
        if self._session is None:
            if self.session_id is None:
                raise DhHlError("no current session (need -s)")
            self._session = self.catalog.get_session(self.session_id)
        return self._session

    @property
    def workspace(self):
        if self._workspace is None:
            if self.session_id is None:
                raise DhHlError("no current session workspace (need -s)")
            self._workspace = SessionWorkspace(
                self.catalog.catalog_dir, self.session_id, catalog=self.catalog)
        return self._workspace

    # -- unambiguous schedule node --------------------------------------
    def unambiguous_schedule(self):
        """The schedule node `status` would report as unambiguous, or None."""
        ws = self.workspace
        if not ws.has_workspace_files():
            return None
        h = ws.workspace_hash
        matching = [n for n in self.catalog.schedules.values() if n.hash == h]
        if not matching:
            return None
        cis = ws.current_idea_state
        if cis.kind == "no_idea":
            for n in matching:
                if n.is_root() and n.timestamp == cis.timestamp:
                    return n
        elif cis.kind == "idea":
            for n in matching:
                if n.parent_id == cis.idea_id:
                    return n
        return None

    def require_unambiguous_schedule(self):
        node = self.unambiguous_schedule()
        if node is None:
            raise DhHlError(
                "no unambiguous schedule node for the current workspace state; "
                "pass an explicit [schedule ID] or fix the workspace/idea state")
        return node

    def resolve_schedule_arg(self, arg):
        """Resolve an optional [schedule ID]: explicit if given, else the
        unambiguous schedule node (which requires a current session)."""
        if arg is not None:
            return self.catalog.resolve_schedule(arg)
        # The default (omitted [schedule ID]) is the session workspace's
        # unambiguous schedule -- so a -C-only tool needs -s to resolve it.  Catch
        # the missing session HERE with an argument-specific message, rather than
        # letting the generic `self.workspace` "need -s" error surface (idea.md /
        # impl.md "Default [schedule ID] argument").
        if self.session_id is None:
            raise DhHlError(
                "-s required to resolve the default schedule node argument "
                "(pass an explicit [schedule ID], or -s to use the session "
                "workspace's schedule)")
        return self.require_unambiguous_schedule()

    # -- current idea node ----------------------------------------------
    def current_idea_node(self):
        """The IdeaNode referenced by 'some current idea', or None for 'no
        idea'.  Raises on parse errors/conflict."""
        cis = self.workspace.current_idea_state
        if cis.kind == "no_idea":
            return None
        if cis.kind == "idea":
            return self.catalog.get_idea(cis.idea_id)
        raise DhHlError(cis.problem_message())

    # -- finish (mutating tools) ----------------------------------------
    def finish(self):
        self.catalog.flush()
        safety.commit()
