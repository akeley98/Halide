"""Shared pytest fixtures for the dh_hl test suite.

These tests are NOT shipped with the package, so they may use pytest/hypothesis
even though the package itself is stdlib-only.

Because a Catalog can only be constructed while its catalog lock is held (the
load-bearing invariant), tests satisfy that lock invariant one of three ways --
see impl.md "Tests" for the full explanation:

* Pure-model white-box tests build a catalog via `open_catalog`, which uses
  `locks._fake_hold_for_tests` to set the lock state with no syscall/files.
* In-process tool tests drive cmd_* through `run_tool`, which resets + re-arms
  the fake lock state per call (real acquire path, `flock` faked via `fake_locks`)
  to model the once-per-process, released-at-exit lock lifecycle.
* Subprocess tests run ./dh_hl for real end-to-end (real locks, real argparse),
  bootstrapping the catalog+session in-process first.

The `_reset_lock_state` autouse fixture returns to lock level NONE between tests.
"""

import itertools
import os
import subprocess
import sys
import types

import pytest

# Make `import dendritic_hl_lib` work regardless of where pytest is invoked.
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

DH_HL = os.path.join(_PKG_ROOT, "dh_hl")

# The Halide directory the tests build against: derived from this checkout's
# layout (the harness package dir lives INSIDE the Halide directory), never
# hard-wired to a home-relative path.  `HALIDE_DIR` is what tests set as a
# session's Halide path; `HALIDE_BUILD_DIR` is its build-output tree, used by the
# `halide`-marked tests' skip guards.
HALIDE_DIR = os.path.dirname(_PKG_ROOT)
HALIDE_BUILD_DIR = os.path.join(HALIDE_DIR, "build")

# A trivial, compilable-looking generator body.  Content only matters to the
# real-Halide tests; everything else just hashes/stores it.
DUMMY_SOURCE = """\
#include "Halide.h"
using namespace Halide;
class Dummy : public Generator<Dummy> {
public:
    Input<Buffer<uint8_t, 2>> input{"input"};
    Output<Buffer<uint8_t, 2>> output{"output"};
    Var x, y;
    void generate() { output(x, y) = input(x, y); }
};
HALIDE_REGISTER_GENERATOR(Dummy, dummy)
"""


@pytest.fixture
def reset_safety():
    """Isolate the safety module's process-global state between tests."""
    from dendritic_hl_lib import safety
    safety._new_entries.clear()
    safety._pending_overwrites.clear()
    safety._new_file_count = 0
    yield safety
    safety._new_entries.clear()
    safety._pending_overwrites.clear()
    safety._new_file_count = 0


def ns(**kwargs):
    """Build an argparse-style namespace for calling cmd_* functions directly."""
    kwargs.setdefault("catalog", None)
    kwargs.setdefault("session", None)
    kwargs.setdefault("schedule", None)
    kwargs.setdefault("parameters", None)
    return types.SimpleNamespace(**kwargs)


@pytest.fixture(autouse=True)
def _reset_lock_state():
    """Every test starts and ends at lock level NONE.  The catalog-lock
    invariant (a Catalog means its lock is held) means stale held-state could
    otherwise let a later test construct a Catalog it shouldn't."""
    from dendritic_hl_lib import locks
    locks._reset_for_tests()
    locks._trace_sink = None
    yield
    locks._reset_for_tests()
    locks._trace_sink = None


def open_catalog(cat_dir):
    """Construct a Catalog with the (test-faked) catalog lock held for it, so
    the "a Catalog means its lock is held" invariant is satisfied without real
    flock or on-disk lock files.  For pure-model tests that build a catalog
    directly rather than through the CLI/Context acquire path."""
    from dendritic_hl_lib import locks
    from dendritic_hl_lib.catalog import Catalog
    locks._fake_hold_for_tests(cat_dir)
    return Catalog(str(cat_dir))


# ---------------------------------------------------------------------------
# Model-level catalog + session construction (mimics Phase 4 new_catalog).
# ---------------------------------------------------------------------------

def make_catalog_session(cat_dir, source=DUMMY_SOURCE, idea_name="seed"):
    """Create a catalog with one top-level session, mirroring the eventual
    `new_catalog`: a root schedule, a seed idea under it, a duplicate schedule
    that is the idea's canonical, and a session seeded with that idea whose
    private workspace holds *source* with current idea = the seed idea (so the
    workspace is initially 'consistent')."""
    from dendritic_hl_lib import locks, safety
    from dendritic_hl_lib.catalog import Catalog
    from dendritic_hl_lib.context import SessionWorkspace
    locks._fake_hold_for_tests(cat_dir)
    try:
        from dendritic_hl_lib.enums import IdeaStateKind, ProblemState
        cat = Catalog(cat_dir)
        cat.ensure_created()
        root = cat.create_schedule(source, parent_idea=None)
        idea = cat.create_idea(root, idea_name, "seed proposal\n")
        dup = cat.create_schedule(source, parent_idea=idea)
        idea.set_canonical(dup.full_id)
        # Mimic new_catalog's default problem (a main RunGenMain problem), so
        # profiling has a problem to run (idea.md "New Catalog Tool").  Created
        # BEFORE the session, matching new_catalog, so the session records it in
        # "enabled problems on opening".
        cat.create_problem(
            ["<RunGenMain>", "--benchmarks=all", "--estimate_all"],
            "default", state=ProblemState.MAIN)
        sess = cat.create_session(idea, None, 0)
        ws = SessionWorkspace(cat.catalog_dir, sess.full_id, catalog=cat)
        ws.initialize(source, (IdeaStateKind.IDEA, idea.full_id))
        # Mimic init_workspace: the seed idea is in the private idea list with
        # pool tag "default", so new_idea on its canonical inherits a tag.
        ws.set_pool_tag(idea.full_id, "default")
        # The real new_catalog leaves the Halide path unset (the user runs
        # set_halide_path afterwards); tests that build need it, so set it to the
        # Halide checkout this harness lives inside (derived, not hard-wired).
        ws.set_halide_path(HALIDE_DIR)
        cat.flush()
        safety.commit()
        return cat.catalog_dir, sess.full_id
    finally:
        locks._reset_for_tests()


_branch_idea_counter = itertools.count()


def branch_fresh_idea(session, name=None):
    """Move the session's current idea onto a fresh child idea that has NO
    canonical yet, and return its full ID.  Models the real 'explore a change'
    workflow: the seed idea already has a canonical (a copy of its parent), so
    `init_build --target workspace` refuses to add more children to it (idea.md
    "Init-Build Tool") -- you branch a new idea off the canonical and explore
    there.  Tests that create a new child schedule via a perturbed workspace must
    first land on such a canonical-less idea.

    *name* defaults to a per-call-unique proposal name, so branching repeatedly
    off the same canonical (e.g. a test that rebuilds several times) never
    collides on the proposal name."""
    if name is None:
        name = "explore_{}".format(next(_branch_idea_counter))
    from dendritic_hl_lib import locks, safety
    from dendritic_hl_lib.catalog import Catalog
    from dendritic_hl_lib.context import SessionWorkspace
    locks._fake_hold_for_tests(session.catalog_dir)
    try:
        cat = Catalog(session.catalog_dir)
        seed = cat.get_idea(cat.get_session(session.session_id).seed_idea_id)
        canonical = cat.get_schedule(seed.canonical)
        idea = cat.create_idea(canonical, name, "explore proposal\n")
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        ws.set_pool_tag(idea.full_id, "default")
        ws.current_idea_state.set_idea(idea.full_id)
        cat.flush()
        safety.commit()
        return idea.full_id
    finally:
        locks._reset_for_tests()


def make_profiler_obj(wall_time_min, *, profiler_version=1, name="p", funcs=None,
                      **extra):
    """A minimal profiler pipeline object (idea.md "Benchmark Sub-object State")
    for synthetic benchmarks: enough for the cost core (`wall_time_min`,
    `profiler_version`), extensible with `funcs=` / `**extra` for the
    profiler-stats tests."""
    obj = {"profiler_version": profiler_version, "name": name,
           "wall_time_min": wall_time_min, "funcs": funcs or []}
    obj.update(extra)
    return obj


def add_synthetic_benchmark_set(cat, specs, *, problem=None, hostname="host",
                                cpu_count=8, profiler_version=1):
    """Create real benchmark sub-objects + a benchmark set from *specs*, with NO
    Halide/profiler run -- the deterministic backbone of the cost tests
    (impl.md "Cost Model Core" / idea.md testing notes).

    *specs* maps ``schedule full id -> [[batch sample, ...] per params index]``.
    A batch sample is either a ``wall_time_min`` number (wrapped into a minimal
    profiler object) or a full profiler dict.  Returns the new benchmark set's
    full ID.  The caller must hold the catalog lock (e.g. via `open_catalog`).

    Each benchmark is tagged with *problem* + its parameters index; a set is
    single-problem, so the whole set shares one problem.  *problem* defaults to
    the catalog's main problem full ID if one exists (so the cost tools'
    default-to-main filter matches), else None (the catalog-agnostic core tests
    use bare catalogs with no problem)."""
    if problem is None:
        try:
            problem = cat.main_problem().full_id
        except Exception:
            problem = None
    data = {}
    for sched_id, per_pidx in specs.items():
        node = cat.get_schedule(sched_id)
        cells = []
        for pidx, batch_samples in enumerate(per_pidx):  # one list per params idx
            row = []
            for sample in batch_samples:
                prof = (sample if isinstance(sample, dict)
                        else make_profiler_obj(
                            sample, profiler_version=profiler_version))
                bench = node.add_benchmark(hostname, {
                    "hostname": hostname, "cpu_count": cpu_count,
                    "parameters": {}, "parameters_index": pidx,
                    "problem": problem, "profiler": prof, "warnings": [],
                    "stdout": ""})
                row.append(bench.full_id)
            cells.append(row)
        data[sched_id] = cells
    return cat.create_benchmark_set(data).full_id


class Sess:
    """Handle to a test catalog+session for driving cmd_* in-process."""

    def __init__(self, catalog_dir, session_id):
        self.catalog_dir = catalog_dir
        self.session_id = session_id
        self.private_dir = os.path.join(catalog_dir, "private", session_id)
        self.workspace_path = os.path.join(self.private_dir, "generator.cpp")

    def ns(self, **kw):
        kw.setdefault("session", self.session_id)
        kw.setdefault("catalog", self.catalog_dir)
        return ns(**kw)

    def write_workspace(self, text):
        os.makedirs(self.private_dir, exist_ok=True)
        with open(self.workspace_path, "w", encoding="utf-8") as f:
            f.write(text)

    def write_params(self, text):
        """Overwrite the workspace generator_parameters.json (str or JSON-able)."""
        import json as _json
        os.makedirs(self.private_dir, exist_ok=True)
        if not isinstance(text, str):
            text = _json.dumps(text)
        with open(os.path.join(self.private_dir, "generator_parameters.json"),
                  "w", encoding="utf-8") as f:
            f.write(text)

    def set_no_idea(self, timestamp):
        """Force the current idea state to 'no_idea' (for root-oriented flows)."""
        p = os.path.join(self.private_dir, "current_idea_state.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("dendritic_hl_root({})\n".format(timestamp))


@pytest.fixture
def session(tmp_path, reset_safety):
    """A catalog with one consistent top-level session (level NONE creation)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    catalog_dir, session_id = make_catalog_session(cat_dir)
    return Sess(catalog_dir, session_id)


# ---------------------------------------------------------------------------
# Fake locking for in-process tool tests.
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_locks(monkeypatch):
    """Patch flock/lock-file open so acquire_* run their real state + ordering
    logic without touching the filesystem or blocking.  Real cross-process
    mutual exclusion is covered by the subprocess tests."""
    import fcntl
    from dendritic_hl_lib import locks
    monkeypatch.setattr(fcntl, "flock", lambda *a, **k: None)
    monkeypatch.setattr(locks, "_open_lock_file", lambda path: -1)
    locks._reset_for_tests()
    locks._trace_sink = None
    yield locks
    locks._reset_for_tests()
    locks._trace_sink = None


@pytest.fixture
def run_tool(fake_locks):
    """Invoke a cmd_* function as if in a fresh process: reset + re-arm the
    (fake) machine lock, resetting the trace log, then call fn(args).  Returns
    the fake locks module so tests can inspect locks._trace_sink."""
    from dendritic_hl_lib import locks

    def _run(fn, args):
        locks._reset_for_tests()
        locks._trace_sink = []
        locks.acquire_machine_shared()
        return fn(args)
    _run.locks = fake_locks
    return _run


@pytest.fixture
def run_cli(tmp_path_factory):
    """Run ./dh_hl as a real subprocess; returns CompletedProcess.

    Isolates XDG_CACHE_HOME to a throwaway dir so the machine lock/handle store
    never touch the real ~/.cache.  Each test gets its own cache dir; a caller
    that needs two concurrent processes to share a machine lock should pass an
    explicit XDG_CACHE_HOME via *env*."""
    cache = str(tmp_path_factory.mktemp("dh_hl_xdg"))

    def _run(*args, env=None, input=None):
        e = dict(os.environ)
        e["XDG_CACHE_HOME"] = cache
        if env:
            e.update(env)
        return subprocess.run(
            [DH_HL, *args], capture_output=True, text=True, env=e, input=input)
    return _run
