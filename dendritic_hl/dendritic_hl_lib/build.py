"""The `init_build` and `build` tools: 3-way Halide build + benchmarking.

`init_build` selects up to three schedule nodes (target / other / anchor) and
records them in the session private workspace's `init_build.json`.  `build` then
compiles + optionally profiles exactly that selection.  Splitting the two lets
`build` do its expensive compile WITHOUT the catalog lock: `init_build` (which
may create the target node) takes the catalog lock and leaves behind the catalog
file paths, so `build` reads them lock-free (impl.md "Session Private Workspace",
"Build/Profile Decisions").

Per the decided build-driver split: ninja builds the param-independent steps
(each node's C++ workspace -> generator executable, plus the shared RunGenMain.o);
Python drives the param-dependent steps serially with subprocess (generator emit
-> link -> optional benchmark run).

Naming inside bin/ is keyed by schedule full ID + parameters index so the three
nodes' many binaries coexist for the shuffled profiling pass (idea.md "Build Tool
Implementation Details").  The reference recipe in reference_build_commands.md
teaches the Halide toolchain steps; it deliberately does NOT track these
catalog-specific file names (this module is their single source of truth).

TEST COUPLING: the module-level `_`-prefixed helpers that shell out to the
toolchain -- `_write_ninja`, `_ninja_build`, `_discover_generator_name`,
`_emit`, `_link`, `_run_benchmark` -- are monkeypatch seams. tests/test_build_fake.py
replaces them (by name, with matching signatures) so build logic can be
exercised without a real Halide build.  Treat their names and signatures as a
lightly load-bearing test contract.  See impl.md "Tests".
"""

import argparse
import json
import os
import random
import subprocess
import sys

from . import ids
from . import locks
from . import profiler_warnings
from . import safety
from .catalog import (Catalog, PROBLEM_LIB, PROBLEM_RUNGENMAIN, best_result,
                      canonical_block_advice, validate_parameters)
from .context import Context, SessionWorkspace, resolve_target
from .enums import Result
from .errors import DhHlError, HalideBuildError
from . import ninja_syntax

# ---- Halide location (magic constants; see impl.md FUTURE notes) -----------
HALIDE_BUILD = os.path.expanduser("~/Halide/build")
HALIDE_ROOT = os.path.expanduser("~/Halide")

_INC_BUILD = os.path.join(HALIDE_BUILD, "include")
_SRC_RUNTIME = os.path.join(HALIDE_ROOT, "src", "runtime")
_SOURCE_TOOLS = os.path.join(HALIDE_ROOT, "tools")
_GENGEN_A = os.path.join(HALIDE_BUILD, "tools", "libHalide_GenGen.a")
_HALIDE_LIBDIR = os.path.join(HALIDE_BUILD, "src")
_RUNGENMAIN_CPP = os.path.join(_SOURCE_TOOLS, "RunGenMain.cpp")

_RUNGENMAIN_OBJ = "RunGenMain.o"   # shared, param- and node-independent
# The standalone Halide runtime object, shared by every RunGenMain binary in a
# build (node- and param-independent, emitted once per bin/).  The pipeline is
# emitted `no_runtime` (see _emit), so this object is what supplies the single
# runtime -- both to the RunGenMain link here AND, by the same logic, to an
# external dlopen runner (which owns its own copy).  This is the "no runtime in
# the artifact; exactly one runtime, owned by whoever runs it" invariant from
# reference_build_commands.md "Path B".  The standalone runtime carries the
# profiler regardless of its target feature, so `target=host` suffices.
_RUNTIME_OBJ = "halide_runtime.o"

_CXX = "c++"

# The fixed, stable `-f` basename for every emitted pipeline (impl.md "Output
# basename (-f) and per-(node, params-index) layout").  Because each (node,
# params index) emits into its OWN subdirectory, the basename never needs to
# encode identity, so it can be this one clean C identifier -- giving a stable
# symbol `dh_hl_pipeline` and header `dh_hl_pipeline.h` usable as-is by
# copy_build_output / a dlopen runner.
_PIPELINE = "dh_hl_pipeline"


# ---------------------------------------------------------------------------
# bin/ naming
# ---------------------------------------------------------------------------
# Node-level artifacts (param-independent) live at the bin/ root, one per node:
# the ninja file and the generator executable, plus the fully shared
# RunGenMain.o.  Per-(node, params-index) emit/link outputs live in a
# subdirectory bin/{full_id}_{i}/ (all named `dh_hl_pipeline.*`), so the fixed
# `-f` basename never clobbers across nodes/params.

def _ninja_name(full_id):
    return full_id + ".ninja"


def _gen_exe_name(full_id):
    return full_id + "_generator"


def _param_subdir(full_id, i):
    """The per-(node, params-index) output subdirectory, relative to bin/."""
    return "{}_{}".format(full_id, i)


def _rungen_bin_rel(full_id, i):
    """The linked RunGenMain binary, relative to bin/."""
    return os.path.join(_param_subdir(full_id, i), _PIPELINE + ".rungen")


def _shared_lib_filename():
    """The emitted shared-library file name (platform extension)."""
    return _PIPELINE + (".dylib" if sys.platform == "darwin" else ".so")


def _shared_lib_rel(full_id, i):
    """The emitted no_runtime shared library, relative to bin/."""
    return os.path.join(_param_subdir(full_id, i), _shared_lib_filename())


# ---------------------------------------------------------------------------
# observability: build commands share the lock trace sink
# ---------------------------------------------------------------------------
# The `build` tool records the toolchain steps it issues onto the SAME ordered
# sink as the lock events (`locks._trace_sink`), so a test can assert both the
# command sequence AND its ordering relative to the lock acquisitions in one
# stream.  Like the lock trace, this is a no-op unless a test sets the sink; it
# is the one observability concession in otherwise test-agnostic build code.
# Event shape: ("build", <phase>, *detail), e.g. ("build", "profile", full_id, i).
# (Only `build` is instrumented; other tools are not, by design, for now.)

def _trace_build(phase, *detail):
    locks._trace(("build", phase) + detail)


# ---------------------------------------------------------------------------
# subprocess helpers
# ---------------------------------------------------------------------------

def _run_streamed(cmd, cwd=None, env=None):
    """Run *cmd*, letting its stdout/stderr flow to ours.  Returns exit code.

    We deliberately do NOT echo the command itself: the toolchain invocations
    carry long absolute include paths and would drown the `dh_hl:` banners in
    noise (idea.md Build Tool).  Compiler/generator output still flows through.

    Flush our own stdout/stderr FIRST so that any `dh_hl:` lines we printed before
    this child are ordered *before* the child's output in a captured stream (our
    Python stdout is block-buffered when piped; the child writes to the same fd).
    Tests rely on this ordering (generator prints vs the `dh_hl:` generator
    banners)."""
    sys.stdout.flush()
    sys.stderr.flush()
    return subprocess.run(cmd, cwd=cwd, env=env).returncode


def _run_capture(cmd, cwd=None):
    """Run *cmd*, capturing combined output.  Returns (rc, text)."""
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    return p.returncode, p.stdout


# ---------------------------------------------------------------------------
# generator parameter formatting
# ---------------------------------------------------------------------------

def _format_param_value(v):
    """Format a generator parameter value as a key=value token payload.

    Whole numbers with %d, non-whole with a full-precision repr (no roundoff);
    see the NB in the Build Tool section of impl.md."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return "%d" % v
    if isinstance(v, float):
        if v.is_integer():
            return "%d" % int(v)
        return repr(v)
    if isinstance(v, str):
        return v
    raise DhHlError("unsupported generator parameter value: " + repr(v))


def _param_tokens(params):
    return ["{}={}".format(k, _format_param_value(v)) for k, v in params.items()]


# ---------------------------------------------------------------------------
# ninja: phase 1 (per-node generator exe) + shared RunGenMain.o
# ---------------------------------------------------------------------------

def _write_ninja(bin_dir, full_id, source_path):
    """Write bin/{full_id}.ninja building this node's generator exe and the
    shared RunGenMain.o.  Returns its path."""
    path = os.path.join(bin_dir, _ninja_name(full_id))
    if os.path.exists(path):
        os.remove(path)  # regenerate; it lives in the gitignored bin/
    with open(path, "w", encoding="utf-8") as f:
        n = ninja_syntax.Writer(f)
        n.comment("Auto-generated by dh_hl; param-independent build steps only.")
        gen_flags = "-std=c++17 -O2 -I{} -I{}".format(_INC_BUILD, _SOURCE_TOOLS)
        gen_ld = "{} -L{} -lHalide -Wl,-rpath,{}".format(
            _GENGEN_A, _HALIDE_LIBDIR, _HALIDE_LIBDIR)
        rungen_flags = ("-std=c++17 -O2 -fno-exceptions -DHALIDE_NO_PNG "
                        "-DHALIDE_NO_JPEG -I{} -I{} -I{} -I.".format(
                            _INC_BUILD, _SRC_RUNTIME, _SOURCE_TOOLS))
        n.variable("cxx", _CXX)
        n.newline()
        n.rule("gen_exe",
               command="$cxx {} $in -o $out {}".format(gen_flags, gen_ld),
               description="GEN-EXE $out")
        n.rule("rungenmain_obj",
               command="$cxx -c {} $in -o $out".format(rungen_flags),
               description="CXX RunGenMain.o")
        n.newline()
        n.build(_gen_exe_name(full_id), "gen_exe", os.path.abspath(source_path))
        n.build(_RUNGENMAIN_OBJ, "rungenmain_obj", _RUNGENMAIN_CPP)
    return path


def _ninja_build(bin_dir, ninja_path, targets):
    cmd = ["ninja", "-f", os.path.basename(ninja_path)] + targets
    return _run_streamed(cmd, cwd=bin_dir)


def _discover_generator_name(bin_dir, gen_exe):
    """Run *gen_exe* with no -g and scrape the single registered name.
    Raises HalideBuildError if the count isn't exactly one."""
    rc, out = _run_capture(["./" + gen_exe], cwd=bin_dir)
    names = []
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if "available Generators are:" in line:
            for rest in lines[i + 1:]:
                if rest.strip():
                    names.append(rest.strip())
                else:
                    break
            break
    if len(names) != 1:
        raise HalideBuildError(
            "expected exactly one registered generator (single-generator "
            "assumption), found {}: {}".format(len(names), names))
    return names[0]


# ---------------------------------------------------------------------------
# phase 2 (emit) + phase 4 (link) + run
# ---------------------------------------------------------------------------

def _emit(bin_dir, gen_exe, gen_name, out_subdir, params, with_stmt):
    """Run the generator, emitting all artifacts into bin/{out_subdir}/ under the
    fixed `-f dh_hl_pipeline` basename.  The subdir isolates this (node, params
    index)'s outputs, so the stable basename never clobbers another's."""
    os.makedirs(os.path.join(bin_dir, out_subdir), exist_ok=True)
    # Emit the pipeline as a `no_runtime` OBJECT (not static_library): the object
    # has undefined halide_* symbols and carries no runtime of its own, so the
    # SAME object serves both the RunGenMain link (which adds the shared runtime
    # object) and the `-shared` link (whose undefined symbols resolve upward into
    # a dlopen runner that owns the runtime).  This is the "no runtime in the
    # artifact" half of the invariant (reference_build_commands.md "Path B").
    emits = ["object", "c_header", "registration"]
    if with_stmt:
        # Both the plain lowered loop nest and the conceptual (pre-lowering) form.
        emits += ["stmt", "conceptual_stmt"]
    cmd = (["./" + gen_exe, "-g", gen_name, "-o", out_subdir, "-f", _PIPELINE]
           + _param_tokens(params)
           + ["-e", ",".join(emits), "target=host-profile-no_runtime"])
    # Point the generator at where to serialize the algorithm `hlpipe` (the
    # pre-scheduling pipeline, for the golden algorithm-equality check).  A
    # generator with the serialize_pipeline snippet writes it; one without simply
    # ignores the env var (idea.md new_golden, reference_build_commands.md).
    env = dict(os.environ)
    env["DENDRITIC_HL_ALGORITHM_HLPIPE"] = os.path.abspath(
        os.path.join(bin_dir, out_subdir, _PIPELINE + ".hlpipe"))
    return _run_streamed(cmd, cwd=bin_dir, env=env)


def _ensure_runtime(bin_dir, gen_exe):
    """Emit the shared standalone Halide runtime object (bin/halide_runtime.o)
    once per bin/, reusing it thereafter.  Node- and param-independent, so any
    compiled generator exe can emit it.  Returns 0 if it is present (already or
    newly emitted), else the generator's nonzero exit code."""
    if os.path.exists(os.path.join(bin_dir, _RUNTIME_OBJ)):
        return 0
    # -r halide_runtime emits the standalone runtime as halide_runtime.o.
    cmd = ["./" + gen_exe, "-r", "halide_runtime", "-o", ".",
           "-e", "object", "target=host"]
    return _run_streamed(cmd, cwd=bin_dir)


def _link(bin_dir, out_subdir):
    """Link RunGenMain.o + the subdir's registration.cpp + the no_runtime
    pipeline object + the shared runtime object into
    bin/{out_subdir}/dh_hl_pipeline.rungen."""
    base = os.path.join(out_subdir, _PIPELINE)
    cmd = [_CXX, "-std=c++17", "-O2", _RUNGENMAIN_OBJ,
           base + ".registration.cpp", base + ".o", _RUNTIME_OBJ,
           "-o", base + ".rungen", "-lpthread", "-ldl"]
    return _run_streamed(cmd, cwd=bin_dir)


def _link_shared(bin_dir, out_subdir):
    """Link the no_runtime pipeline object into a shared library
    bin/{out_subdir}/dh_hl_pipeline.{so,dylib}.  The library keeps its halide_*
    symbols UNDEFINED (no embedded runtime); on macOS `-undefined dynamic_lookup`
    lets them bind at dlopen time to the runner that owns the runtime (on Linux
    -shared already permits undefined symbols)."""
    base = os.path.join(out_subdir, _PIPELINE)
    cmd = [_CXX, "-shared", base + ".o", "-o",
           os.path.join(out_subdir, _shared_lib_filename())]
    if sys.platform == "darwin":
        cmd += ["-Wl,-undefined,dynamic_lookup"]
    return _run_streamed(cmd, cwd=bin_dir)


def _resolve_run(bin_dir, problem_argv, rungen_rel, shared_rel):
    """Map a problem's argv + this binary's paths to (cmd, extra_env) for a run
    (idea.md "Problem Object State" / "New Problem Tool").

    `<RunGenMain>` becomes the absolute path to the linked RunGenMain binary;
    `<Lib>` becomes the absolute path to the emitted shared library.  For a
    custom-runner problem (argv[0] is not `<RunGenMain>`), the shared-library path
    is ALSO exported as DENDRITIC_HL_OUTPUT_LIB, so a runner can take it from the
    environment instead of (or in addition to) `<Lib>`.  All substituted paths are
    absolute, so the run's cwd does not matter."""
    rungen_abs = os.path.abspath(os.path.join(bin_dir, rungen_rel))
    shared_abs = os.path.abspath(os.path.join(bin_dir, shared_rel))
    cmd = []
    for tok in problem_argv:
        if tok == PROBLEM_RUNGENMAIN:
            cmd.append(rungen_abs)
        elif tok == PROBLEM_LIB:
            cmd.append(shared_abs)
        else:
            cmd.append(tok)
    extra_env = {}
    if problem_argv[0] != PROBLEM_RUNGENMAIN:
        extra_env["DENDRITIC_HL_OUTPUT_LIB"] = shared_abs
    return cmd, extra_env


def _run_benchmark(bin_dir, cmd, extra_env, json_out_path, warnings_out_path):
    """Run one profiling command (the problem's resolved argv).  Captures the
    binary's stdout -- it is redirected into the benchmark sub-object (later read
    back by `view_benchmark_stdout`), NOT echoed to the harness stdout (idea.md
    Build Tool) -- while letting stderr flow.  Returns (rc, stdout_text)."""
    env = dict(os.environ)
    env["HL_PROFILER_JSON_OUTPUT"] = json_out_path
    # Andrew Adams's profiler doesn't put warnings in the main JSON yet; a
    # separate secret-menu env var names a file of per-pipeline warnings (see
    # reference_build_commands.md "Warnings Output").
    env["HL_PROFILER_JSON_TEMPORARY_WARNINGS"] = warnings_out_path
    env.update(extra_env)
    # RunGen is deliberately NOT run with --quiet: its `halide_print:`
    # profiler-stats table stays in the captured stdout (an easy read next to the
    # JSON tools; it lands in the benchmark sub-object, not the harness output).
    p = subprocess.run(cmd, cwd=bin_dir, env=env, stdout=subprocess.PIPE,
                       universal_newlines=True)
    return p.returncode, p.stdout or ""


# ---------------------------------------------------------------------------
# init_build: select up to three nodes, record them for `build`
# ---------------------------------------------------------------------------

# init_build.json format (session private workspace).  A JSON object with keys
# "target", "other", "anchor"; each is null (disabled) or an object:
#   {"role": <role>, "id": <schedule full ID>,
#    "source": <generator.cpp path, relative to the catalog dir>,
#    "parameters": <generator_parameters.json path, relative to the catalog dir>}
# `build` reads these catalog-relative paths WITHOUT the catalog lock.
_INIT_BUILD_FILE = "init_build.json"


def _remove_selection(private_dir):
    """Remove the session's init_build selection if present (idempotent)."""
    try:
        os.remove(os.path.join(private_dir, _INIT_BUILD_FILE))
    except FileNotFoundError:
        pass


def invalidate_selection(catalog_dir, session_id):
    """Drop a session's init_build selection.  See invalidate_selection_best_effort;
    runs WITHOUT the session lock (impl.md "Lock Hierarchy")."""
    _remove_selection(SessionWorkspace(catalog_dir, session_id).private_dir)


def invalidate_selection_best_effort(argv):
    """Pre-argparse footgun guard for `init_build`, called from `main()` BEFORE the
    strict parse (*argv* is the tokens after the `init_build` command word).

    A malformed `init_build` -- a stray positional, an unknown flag -- makes
    argparse `SystemExit` before `cmd_init_build` runs, which would otherwise
    leave an earlier successful selection on disk for `build` to silently reuse
    (idea.md "Init-Build Tool" footgun).  So we clear the selection here, up
    front: a lenient parse for just `-C`/`-s`, and if the session resolves, drop
    its `init_build.json`.

    Runs WITHOUT the session lock, deliberately (impl.md "Lock Hierarchy").  The
    session lock is non-blocking and *exits with failure* when unheld, so taking
    it here could itself fail -- exactly the kind of low-level failure that must
    not defeat the guard.  The remove is idempotent and session-private (not
    catalog-tracked, so no lock is load-bearing for it).  Best-effort: if `-C`/`-s`
    is absent or won't resolve we do nothing -- a later `build` with that same
    `-s` fails the same way, so nothing stale is used.  `init_build -h`/`--help`
    is a help request, not a build attempt, so it is left alone."""
    if "-h" in argv or "--help" in argv:
        return
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("-C", "--catalog")
    pre.add_argument("-s", "--session")
    try:
        known, _ = pre.parse_known_args(argv)
        catalog_dir, session_id = resolve_target(known)
    except (DhHlError, SystemExit, OSError):
        return
    if session_id is not None:
        invalidate_selection(catalog_dir, session_id)


def _init_build_target_spec(args):
    """The target selection spec, accepting EITHER `--target X` or a bare
    positional `X` (idea.md "Init-Build Tool": the positional was added because
    agents passed the target positionally, overgeneralizing from other commands).
    Defaults to `workspace`; giving both forms at once is an error."""
    flag = getattr(args, "target", None)
    pos = getattr(args, "target_pos", None)
    if flag is not None and pos is not None:
        raise DhHlError(
            "give the target once: either the positional schedule ID or "
            "--target, not both")
    return flag if flag is not None else (pos if pos is not None else "workspace")


def _rel_to_catalog(catalog_dir, path):
    return os.path.relpath(path, catalog_dir)


def _node_entry(catalog, role, node):
    sch_dir = node.dir
    return {
        "role": role,
        "id": node.full_id,
        # A human-friendly short ID for `build`'s banners: `build`'s compile
        # phase runs WITHOUT the catalog lock, so it cannot format short IDs
        # itself -- we precompute it here (under the catalog lock) and carry it
        # in the selection.  A display label only; harmless if it later goes
        # slightly stale relative to the catalog.
        "short_id": catalog.format_schedule_id(node),
        "source": _rel_to_catalog(catalog.catalog_dir,
                                  os.path.join(sch_dir, "generator.cpp")),
        "parameters": _rel_to_catalog(
            catalog.catalog_dir,
            os.path.join(sch_dir, "generator_parameters.json")),
    }


def _resolve_target(ctx, spec):
    """Resolve --target: an explicit schedule ID (magic values included), or
    the special `workspace`."""
    if spec != "workspace":
        return ctx.resolve_schedule(spec)
    unamb = ctx.unambiguous_schedule()
    if unamb is not None:
        return unamb
    idea = ctx.current_idea_node()
    if idea is None:
        raise DhHlError(
            "no unambiguous schedule node and no current idea node for the "
            "workspace; use `dh_hl set_idea <idea>` to pick an idea (then this "
            "adds a child schedule), or `dh_hl new_root` to start a new root")
    # If the current idea already has a canonical schedule, refuse rather than
    # add another child under it, giving the same advice as `canon` (idea.md
    # "Init-Build Tool"): branch a new idea off the canonical and explore there.
    if idea.canonical is not None:
        raise DhHlError(canonical_block_advice(ctx.catalog, idea.canonical))
    # Add a new child schedule node holding a copy of the workspace files.
    ws = ctx.workspace
    return ctx.catalog.create_schedule(
        ws.workspace_source, parent_idea=idea,
        params_text=ws.workspace_params_text)


def _resolve_other(ctx, spec, target):
    """Resolve --other: `none` (disabled), `parent` (target's parent idea's
    parent schedule), or an explicit ID."""
    if spec == "none":
        return None
    if spec == "parent":
        if target.is_root():
            return None
        idea = target.parent_idea()
        if idea is None:
            return None
        return idea.parent_schedule()
    return ctx.resolve_schedule(spec)


def _resolve_anchor(ctx, spec):
    """Resolve --anchor: `none` (disabled); `auto` (the session's current anchor,
    or disabled if none); `always` (the current anchor, error if none); or an
    explicit schedule ID."""
    if spec == "none":
        return None
    if spec in ("auto", "always"):
        anchor_id = ctx.workspace.current_anchor_schedule_id
        if anchor_id is None:
            if spec == "always":
                raise DhHlError(
                    "--anchor always: the current session has no current anchor "
                    "(set one with `dh_hl set_current_anchor`)")
            return None
        return ctx.catalog.get_schedule(anchor_id)
    return ctx.resolve_schedule(spec)


def cmd_init_build(args):
    # init_build may CREATE the target node, so it needs the catalog lock (and
    # the session lock, since it writes the private workspace).
    ctx = Context.for_session(args, session_lock=True)
    ctx.workspace.ensure_private_dir()

    # Footgun guard (idea.md Init-Build Tool): invalidate any prior selection
    # BEFORE the fallible resolution below, so a failed init_build can't leave an
    # earlier success's selection lying around for `build` to silently reuse.
    # The invariant `build` relies on: init_build.json exists iff the session's
    # most recent init_build succeeded (it is rewritten on success just below).
    # This is a per-session file, so it never affects other sessions.  (Failures
    # too early to reach here -- e.g. argparse rejecting the invocation -- are
    # caught by main()'s pre-parse invalidate_selection_best_effort.)
    private_dir = ctx.workspace.private_dir
    _remove_selection(private_dir)

    target = _resolve_target(ctx, _init_build_target_spec(args))
    other = _resolve_other(ctx, getattr(args, "other", None) or "parent", target)
    anchor = _resolve_anchor(ctx, getattr(args, "anchor", None) or "auto")

    selection = {
        "target": _node_entry(ctx.catalog, "target", target),
        "other": _node_entry(ctx.catalog, "other", other) if other else None,
        "anchor": _node_entry(ctx.catalog, "anchor", anchor) if anchor else None,
    }
    # Persist BEFORE finish so the new target node is flushed first.
    path = os.path.join(private_dir, _INIT_BUILD_FILE)
    safety.new_file(path, json.dumps(selection, indent=1) + "\n",
                    overwrite_allowed=True)
    ctx.finish()

    cat = ctx.catalog
    print("dh_hl: init_build target: " + cat.format_schedule_id(target))
    print("dh_hl: init_build other: "
          + (cat.format_schedule_id(other) if other else "(disabled)"))
    print("dh_hl: init_build anchor: "
          + (cat.format_schedule_id(anchor) if anchor else "(disabled)"))


# ---------------------------------------------------------------------------
# build: compile + optionally profile the init_build selection
# ---------------------------------------------------------------------------

def _parse_only(spec):
    """Parse --only into ("all"|"target"|("index", N))."""
    if spec is None or spec == "all":
        return ("all", None)
    if spec == "target":
        return ("target", None)
    try:
        n = int(spec)
    except ValueError:
        raise DhHlError("--only must be 'all', 'target', or an integer")
    if n < 0:
        raise DhHlError("--only index must be non-negative")
    return ("index", n)


class _NodeBuild:
    """Per-node build bookkeeping for one `build` run."""
    def __init__(self, entry, catalog_dir):
        self.role = entry["role"]
        self.full_id = entry["id"]
        # Short display ID precomputed by init_build (see _node_entry); older
        # selections without it fall back to the full ID.
        self.short_id = entry.get("short_id", entry["id"])
        self.source_path = os.path.join(catalog_dir, entry["source"])
        self.params_path = os.path.join(catalog_dir, entry["parameters"])
        with open(self.source_path, "r", encoding="utf-8") as f:
            self.source = f.read()
        with open(self.params_path, "r", encoding="utf-8") as f:
            self.params = validate_parameters(json.load(f))
        if not self.params:
            raise HalideBuildError(
                "schedule node {} has 0 generator parameters objects; nothing "
                "to build".format(self.full_id))
        self.cpp_ok = False
        # A harness/environment failure (RunGenMain.o, generator-count) is NOT a
        # build outcome to catalogue -> leave the node's result untouched.
        self.harness_error = False
        self.gen_name = None
        self.gen_ok = {}        # params index -> bool (emit succeeded)
        self.linked = {}        # params index -> bool (RunGenMain link succeeded)
        self.shared_ok = {}     # params index -> bool (shared-lib link succeeded)
        self.any_run_failed = False


def _load_selection(ws):
    path = os.path.join(ws.private_dir, _INIT_BUILD_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise DhHlError(
            "no successful init_build for this session; run `dh_hl init_build` "
            "first (a failed init_build clears any earlier selection, so re-run "
            "it after fixing the failure)")


def cmd_build(args):
    # Phase 0: resolve session + take session/concurrent-machine locks (NOT the
    # catalog lock -- the compile below must not block other agents).
    catalog_dir, session_id = resolve_target(args)
    if session_id is None:
        raise DhHlError("this command requires a session (-s)")
    if not os.path.isdir(catalog_dir):
        raise DhHlError("no catalog directory: " + catalog_dir)
    locks.acquire_session(catalog_dir, session_id)
    ws = SessionWorkspace(catalog_dir, session_id)
    ws.ensure_private_dir()
    bin_dir = ws.bin_dir
    os.makedirs(bin_dir, exist_ok=True)

    only_kind, only_index = _parse_only(getattr(args, "only", None))
    profile_batches = getattr(args, "profile", 0) or 0
    if profile_batches < 0:
        raise DhHlError("--profile batch count must be non-negative")

    selection = _load_selection(ws)
    target = _NodeBuild(selection["target"], catalog_dir)
    other = (_NodeBuild(selection["other"], catalog_dir)
             if selection.get("other") else None)
    anchor = (_NodeBuild(selection["anchor"], catalog_dir)
              if selection.get("anchor") else None)

    # Which nodes to build: --only all pulls in other+anchor; else just target.
    nodes = [target]
    if only_kind == "all":
        nodes += [n for n in (other, anchor) if n is not None]

    # Which parameter indices to build per node.
    if only_kind == "index":
        if only_index >= len(target.params):
            raise HalideBuildError(
                "--only {} is out of range for target's {} parameters "
                "object(s)".format(only_index, len(target.params)))
        param_indices = {target.full_id: [only_index]}
    else:
        param_indices = {n.full_id: list(range(len(n.params))) for n in nodes}

    all_ok = _compile_phase(bin_dir, nodes, param_indices)

    # Profiling is all-or-nothing on the build: if any generator/link failed,
    # skip it entirely (don't take the exclusive machine lock for a doomed run --
    # idea.md Build Tool pseudocode "or any build/generate failed").
    do_profile = profile_batches > 0 and all_ok

    # Phase 2: profiling (upgrade to exclusive machine lock first if profiling),
    # then the catalog lock, then find nodes + record.
    if do_profile:
        locks.upgrade_machine_exclusive()
    locks.acquire_catalog(catalog_dir)
    ctx = Context(Catalog(catalog_dir), session_id)
    catalog = ctx.catalog
    sched = {n.full_id: catalog.get_schedule(n.full_id) for n in nodes}

    problem_indexes = {}   # problem full ID -> dense bench index
    problem_run_ok = {}    # problem full ID -> every run for it succeeded
    selected = []
    if do_profile:
        selected = catalog.select_problems(getattr(args, "problem", None))
        if not selected:
            print("dh_hl: warning: no problems selected/enabled; nothing to "
                  "profile", file=sys.stderr)
        problem_indexes, problem_run_ok, prof_ok = _profile_phase(
            bin_dir, nodes, param_indices, sched, catalog, profile_batches,
            selected, ws)
        all_ok = all_ok and prof_ok

    # Phase 3: result-state updates (monotone; never regress).  A harness error
    # (not a build outcome) leaves the node's result untouched.
    for n in nodes:
        if n.harness_error:
            continue
        result = _compute_result(n, param_indices[n.full_id], only_kind)
        node = sched[n.full_id]
        node.set_result(best_result(node.result, result))

    # Benchmark sets: for an --only all OR --only target run that profiled >=1
    # batch, ONE set per selected problem whose runs ALL succeeded (idea.md
    # "Build Tool"): so every set is single-problem (its cached problem is
    # uniform).  (--only <int> never makes a set.)  A problem whose runs all
    # succeeded has a dense index by construction.
    made = []
    if do_profile and only_kind in ("all", "target"):
        for problem in selected:
            if problem_run_ok.get(problem.full_id):
                bs = catalog.create_benchmark_set(problem_indexes[problem.full_id])
                ws.add_private_benchmark_set(bs.full_id, catalog)
                made.append((problem, bs))
    ctx.finish()
    for problem, bs in made:
        print("dh_hl: benchmark set for problem "
              + catalog.format_problem_id(problem))
        print("dh_hl: Benchmark set ID: " + bs.full_id)

    sys.exit(0 if all_ok else 1)


def _compile_phase(bin_dir, nodes, param_indices):
    """Phases 1a (per-node C++ generator exe + shared RunGenMain.o) and 1b
    (per-(node, params-index) emit + link).  Mutates the _NodeBuild records.
    Returns whether every attempted subprocess succeeded."""
    all_ok = True
    # 1a: compile each node's generator exe (and the shared RunGenMain.o).
    for n in nodes:
        print("dh_hl: begin C++ compile: " + n.short_id)
        _trace_build("cpp_compile", n.full_id)
        ninja_path = _write_ninja(bin_dir, n.full_id, n.source_path)
        gen_exe = _gen_exe_name(n.full_id)
        if _ninja_build(bin_dir, ninja_path, [gen_exe]) != 0:
            print("dh_hl: end C++ compile fail")
            all_ok = False
            continue
        if _ninja_build(bin_dir, ninja_path, [_RUNGENMAIN_OBJ]) != 0:
            # Harness/environment failure, not a build outcome (impl.md).
            n.harness_error = True
            print("dh_hl: end C++ compile fail (RunGenMain.o)")
            all_ok = False
            continue
        try:
            n.gen_name = _discover_generator_name(bin_dir, gen_exe)
        except HalideBuildError as e:
            # Workspace-authoring problem, not a build outcome (impl.md): leave
            # the node's result untouched.
            n.harness_error = True
            print("dh_hl: " + str(e), file=sys.stderr)
            print("dh_hl: end C++ compile fail")
            all_ok = False
            continue
        n.cpp_ok = True
        print("dh_hl: end C++ compile success")

    # 1b: per-(node, params-index) emit + link.
    for n in nodes:
        if not n.cpp_ok:
            continue
        for i in param_indices[n.full_id]:
            subdir = _param_subdir(n.full_id, i)
            print("dh_hl: begin Halide generator {}: {}".format(i, n.short_id))
            print("dh_hl: params={}".format(json.dumps(n.params[i])))
            _trace_build("emit", n.full_id, i)
            # stmt/conceptual_stmt are emitted for EVERY built pipeline now (so
            # `copy_build_output stmt` works for any built node), not just the
            # target (idea.md Build Tool).
            if _emit(bin_dir, _gen_exe_name(n.full_id), n.gen_name, subdir,
                     n.params[i], with_stmt=True) != 0:
                n.gen_ok[i] = False
                all_ok = False
                print("dh_hl: end Halide generator {} fail".format(i))
                continue
            n.gen_ok[i] = True
            # RunGenMain static-link binary (needs the shared runtime object).
            _ensure_runtime(bin_dir, _gen_exe_name(n.full_id))
            _trace_build("link", n.full_id, i)
            if _link(bin_dir, subdir) != 0:
                n.linked[i] = False
                all_ok = False
                print("dh_hl: end Halide generator {} fail (link)".format(i))
                continue
            n.linked[i] = True
            # no_runtime shared library, for an external dlopen runner (a <Lib>
            # problem).  Built alongside RunGenMain (idea.md Build Tool: "N shared
            # library and RunGenMain binaries are built").
            if _link_shared(bin_dir, subdir) != 0:
                n.shared_ok[i] = False
                all_ok = False
                print("dh_hl: end Halide generator {} fail (shared lib)".format(i))
                continue
            n.shared_ok[i] = True
            print("dh_hl: end Halide generator {} success".format(i))
    return all_ok


def _profile_phase(bin_dir, nodes, param_indices, sched, catalog, batches,
                   problems, ws):
    """For each problem, run *batches* interleaved profiling passes over every
    linked binary, attaching a benchmark sub-object (tagged with the problem full
    ID + parameters index) to each binary's source schedule node.

    Returns (problem_indexes, problem_run_ok, all_ok):
      problem_indexes[problem full ID][sched full ID][slot][batch] = benchmark ID
      problem_run_ok[problem full ID] = every run for that problem succeeded
    A problem whose runs all succeeded has a dense index (idea.md Build Tool)."""
    hostname = ids.stable_hostname()
    file_hostname = ids.sanitize_component(hostname)

    # The list of runnable (node, params index) binaries (shared across problems).
    binaries = []
    for n in nodes:
        for slot, i in enumerate(param_indices[n.full_id]):
            if n.linked.get(i):
                binaries.append((n, i, slot))

    all_ok = True
    problem_indexes = {}
    problem_run_ok = {}
    json_out = os.path.abspath(os.path.join(bin_dir, "profile_out.json"))
    warnings_out = os.path.abspath(os.path.join(bin_dir, "profile_warnings.json"))
    for problem in problems:
        bench_index = {n.full_id: [[None] * batches
                                   for _ in param_indices[n.full_id]]
                       for n in nodes}
        prob_ok = True
        for batch in range(batches):
            random.shuffle(binaries)  # interleaved: fresh order each batch
            _trace_build("batch", batch)
            for n, i, slot in binaries:
                for p in (json_out, warnings_out):
                    if os.path.exists(p):
                        os.remove(p)
                _trace_build("profile", n.full_id, i)
                cmd, extra_env = _resolve_run(
                    bin_dir, problem.argv, _rungen_bin_rel(n.full_id, i),
                    _shared_lib_rel(n.full_id, i))
                rc, stdout_text = _run_benchmark(
                    bin_dir, cmd, extra_env, json_out, warnings_out)
                ok = rc == 0
                # The profile phase holds the catalog lock, so format the short
                # IDs live from the resolved node/problem.
                print("dh_hl: Profiled {}, binary {}, problem {} ({})".format(
                    catalog.format_schedule_id(sched[n.full_id]), i,
                    catalog.format_problem_id(problem),
                    "success" if ok else "fail"))
                if not ok:
                    n.any_run_failed = True
                    all_ok = False
                    prob_ok = False
                    continue
                try:
                    bench_obj = _build_benchmark_obj(
                        json_out, warnings_out, hostname, n.params[i], i,
                        problem.full_id, stdout_text, catalog.fresh_timestamp())
                except HalideBuildError as e:
                    print("dh_hl: skipping benchmark: " + str(e), file=sys.stderr)
                    n.any_run_failed = True
                    all_ok = False
                    prob_ok = False
                    continue
                bench = sched[n.full_id].add_benchmark(file_hostname, bench_obj)
                bench_index[n.full_id][slot][batch] = bench.full_id
                # Record this benchmark in the session's benchmark-short-ID shard
                # so it prints (and later resolves) as private.{schedule}.{i}.{n}
                # (idea.md "Benchmark short ID").  `record` returns this
                # benchmark's `n`, so we format the short ID directly -- no reverse
                # lookup over the (potentially huge) benchmark database.
                bench_n = ws.record_benchmark(n.full_id, i, bench.full_id, catalog)
                short_id = ws.format_benchmark_short_id(
                    sched[n.full_id], i, bench_n, catalog)
                # "... with" ties this line to the "Profiled ..." line just above,
                # so the benchmark ID isn't misread as belonging to the profiler's
                # own stdout printed around it (idea.md Build Tool pseudocode).
                print("dh_hl: ... with Benchmark ID: " + short_id)
        problem_indexes[problem.full_id] = bench_index
        problem_run_ok[problem.full_id] = prob_ok
    return problem_indexes, problem_run_ok, all_ok


def _build_benchmark_obj(json_out, warnings_out, hostname, params,
                         parameters_index, problem_id, stdout_text, timestamp):
    # A runner can exit 0 yet emit no (or a corrupt) profiler JSON -- e.g. a
    # custom <Lib> runner that skips the profiler teardown.  That is a catalogued
    # BAD OUTCOME, not a harness failure: raise HalideBuildError so the profile loop
    # catches it (skips this benchmark, keeps going), never an uncaught crash that
    # would roll back the whole build (idea.md Build Tool).
    try:
        with open(json_out, "r", encoding="utf-8") as f:
            prof = json.load(f)
    except FileNotFoundError:
        raise HalideBuildError(
            "the runner emitted no profiler JSON (expected at {})".format(
                json_out))
    except ValueError as e:
        raise HalideBuildError(
            "the runner's profiler JSON was unparseable: {}".format(e))
    pipelines = prof.get("pipelines")
    if not isinstance(pipelines, list) or len(pipelines) != 1:
        raise HalideBuildError(
            "profiler JSON must have exactly one pipeline (got {})".format(
                None if not isinstance(pipelines, list) else len(pipelines)))
    try:
        cpu_count = os.cpu_count() or 0
    except NotImplementedError:
        cpu_count = 0
    return {
        "hostname": hostname,
        "cpu_count": cpu_count,
        "timestamp": timestamp,
        "parameters": params,
        "parameters_index": parameters_index,
        "problem": problem_id,
        "profiler": pipelines[0],
        "warnings": profiler_warnings.warnings_from_temp_file(warnings_out),
        "stdout": stdout_text,
    }


def _compute_result(n, indices, only_kind):
    """The result state this run establishes for node *n* (idea.md Build Tool
    pseudocode step 3).  `success` means every Halide binary was BUILT (all
    generators emitted); linking RunGenMain and running a benchmark are NOT part
    of the result -- run outcomes are per-problem benchmark facts, checked by
    should_accept, not node state.  best_result() at the call site keeps it
    monotone."""
    if not n.cpp_ok:
        return Result.CPP_ERROR
    # --only [int] builds a single binary, so success is not provable.
    if only_kind == "index" or any(not n.gen_ok.get(i) for i in indices):
        return Result.HALIDE_ERROR
    return Result.SUCCESS


# ---------------------------------------------------------------------------
# copy_build_output: fetch a build artifact from the session bin/
# ---------------------------------------------------------------------------

# what -> file name inside the per-(node, params index) subdir.  `generator` is
# node-level (param-independent, handled specially); `shared_library` uses the
# platform extension.
_COPY_PARAM_FILES = {
    "algorithm_hlpipe": _PIPELINE + ".hlpipe",
    "stmt": _PIPELINE + ".stmt",
    "conceptual_stmt": _PIPELINE + ".conceptual.stmt",
    "header": _PIPELINE + ".h",
    "RunGenMain": _PIPELINE + ".rungen",
}
COPY_BUILD_WHATS = ["generator"] + list(_COPY_PARAM_FILES) + ["shared_library"]


def _build_output_rel(full_id, what, params_index):
    """Path (relative to the session bin/) of the *what* build output for a
    (schedule, params index).  `generator` is param-independent."""
    if what == "generator":
        return _gen_exe_name(full_id)
    subdir = _param_subdir(full_id, params_index)
    if what == "shared_library":
        return os.path.join(subdir, _shared_lib_filename())
    return os.path.join(subdir, _COPY_PARAM_FILES[what])


def _copy_output_params_index(num_params, what, parameters_arg):
    """Resolve the parameters index for copy_build_output: None for the
    param-independent `generator`; otherwise `--parameters` is required when the
    node has >1 params object, and defaults to 0 for a single one (idea.md)."""
    if what == "generator":
        return None
    if num_params > 1 and parameters_arg is None:
        raise DhHlError(
            "--parameters {{index}} is required: the schedule node has {} "
            "generator parameters objects".format(num_params))
    idx = parameters_arg if parameters_arg is not None else 0
    if not 0 <= idx < num_params:
        raise DhHlError("--parameters {} out of range (0..{})".format(
            idx, num_params - 1))
    return idx


def _copy_file_out(src, dst):
    """Copy *src* bytes to *dst* ('-' = stdout).  Bytes, not text: outputs may be
    binaries (generator exe, shared library, RunGenMain)."""
    with open(src, "rb") as f:
        data = f.read()
    if dst == "-":
        sys.stdout.buffer.write(data)
    else:
        with open(dst, "wb") as f:
            f.write(data)


def cmd_copy_build_output(args):
    # Reads the session's private bin/ (built outputs) + resolves the schedule.
    # Read-only, so it does NOT take the session lock (like `status`).
    ctx = Context.for_session(args, session_lock=False)
    node = ctx.resolve_schedule(getattr(args, "schedule", None))
    what = args.what
    idx = _copy_output_params_index(
        len(node.parameters), what, getattr(args, "parameters", None))
    src = os.path.join(ctx.workspace.bin_dir,
                       _build_output_rel(node.full_id, what, idx))
    if not os.path.isfile(src):
        raise DhHlError(
            "build output {!r} not found for {} in this session (run "
            "`dh_hl build` first{}): {}".format(
                what, ctx.catalog.format_schedule_id(node),
                "; and the generator must emit the algorithm hlpipe"
                if what == "algorithm_hlpipe" else "", src))
    _copy_file_out(src, args.output)
