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

from . import ids
from . import locks
from . import safety
from .catalog import (Catalog, CurrentIdeaState, DEFAULT_PARAMETERS,
                      dump_parameters)
from .errors import DhHlError


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

    @property
    def current_idea_state(self):
        if self._current_idea is None:
            self._current_idea = CurrentIdeaState(self.private_dir, self.catalog)
        return self._current_idea

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
        try:
            with open(self.current_anchor_path, "r", encoding="utf-8") as f:
                v = f.read().strip()
        except FileNotFoundError:
            return None
        return v or None

    def set_current_anchor(self, schedule_id, *, allow=True):
        """Set (or clear, when *schedule_id* is None) the current anchor.  An
        empty file encodes 'no anchor' (we never delete files)."""
        self.ensure_private_dir()
        safety.write_allowed(self.current_anchor_path,
                             (schedule_id + "\n") if schedule_id else "",
                             allow=allow)

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
    # Stored as private/{id}/private_ideas.json: a JSON object whose keys are the
    # idea node full IDs in the list and whose values are their pool tags
    # (strings).  Unordered (the future cost-ranked view will sort at read time).
    # Cost is NOT stored here; it's derived when needed.  See impl.md "Session
    # Private Workspace".
    @property
    def private_ideas_path(self):
        return os.path.join(self.private_dir, "private_ideas.json")

    def read_private_ideas(self):
        """The private idea list as a dict {idea full ID -> pool tag}."""
        try:
            with open(self.private_ideas_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _write_private_ideas(self, ideas):
        self.ensure_private_dir()
        safety.write_allowed(self.private_ideas_path,
                             json.dumps(ideas, indent=1) + "\n")

    def has_private_idea(self, idea_id):
        return idea_id in self.read_private_ideas()

    def get_pool_tag(self, idea_id):
        """The pool tag of *idea_id*; error if it isn't in the private list."""
        ideas = self.read_private_ideas()
        if idea_id not in ideas:
            raise DhHlError(
                "idea is not in the session's private idea list: " + idea_id)
        return ideas[idea_id]

    def set_pool_tag(self, idea_id, pool_tag):
        """Set *idea_id*'s pool tag, adding it to the list if necessary."""
        ideas = self.read_private_ideas()
        ideas[idea_id] = pool_tag
        self._write_private_ideas(ideas)

    def hide_private_idea(self, idea_id):
        """Prepend a '.' to the idea's pool tag; error if not in the list."""
        self.set_pool_tag(idea_id, "." + self.get_pool_tag(idea_id))

    def rename_pool_tag(self, before, after):
        """Retag every idea whose pool tag is *before* to *after*.  Returns the
        count updated."""
        ideas = self.read_private_ideas()
        n = 0
        for k, v in ideas.items():
            if v == before:
                ideas[k] = after
                n += 1
        if n:
            self._write_private_ideas(ideas)
        return n

    def remove_private_idea(self, idea_id):
        """Remove *idea_id* from the private idea list, erroring if absent.
        Retained as an internal helper (idea.md close_session note)."""
        ideas = self.read_private_ideas()
        if idea_id not in ideas:
            raise DhHlError(
                "idea is not in the session's private idea list: " + idea_id)
        del ideas[idea_id]
        self._write_private_ideas(ideas)

    # -- private benchmark set list -------------------------------------
    # Stored as private/{id}/private_benchmark_sets.json: a JSON object whose
    # keys are benchmark set full IDs.  Values are currently empty objects {}
    # (they become cached statistics once cost is implemented).
    @property
    def private_benchmark_sets_path(self):
        return os.path.join(self.private_dir, "private_benchmark_sets.json")

    def read_private_benchmark_sets(self):
        try:
            with open(self.private_benchmark_sets_path, "r",
                      encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _write_private_benchmark_sets(self, sets):
        self.ensure_private_dir()
        safety.write_allowed(self.private_benchmark_sets_path,
                             json.dumps(sets, indent=1) + "\n")

    def add_private_benchmark_set(self, set_id):
        """Add *set_id* to the private benchmark set list (value is {} for now).
        Centralized helper so a future cost-init can hook here."""
        sets = self.read_private_benchmark_sets()
        sets[set_id] = {}
        self._write_private_benchmark_sets(sets)


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
