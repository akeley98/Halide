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

import json
import os
import random
import re
import shutil
import subprocess
import sys

from . import ids
from . import locks
from . import profiler_warnings
from . import safety
from .catalog import Catalog, best_result, validate_parameters
from .context import Context, SessionWorkspace, resolve_target
from .errors import DhHlError, HarnessError
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

_CXX = "c++"


# ---------------------------------------------------------------------------
# bin/ naming (keyed by schedule full ID + parameters index)
# ---------------------------------------------------------------------------

def _ninja_name(full_id):
    return full_id + ".ninja"


def _gen_exe_name(full_id):
    return full_id + "_generator"


def _emit_basename(full_id, i):
    """-f basename for one (node, params-index): all emitted artifacts
    (`{base}.a`, `{base}.registration.cpp`, `{base}.stmt`, ...) share it, so
    every (node, i) has a distinct, coexisting set.

    Halide bakes this basename into C identifiers in registration.cpp, so it
    MUST be a valid C identifier.  A schedule full ID starts with a digit and
    contains '-', so sanitize it (non-alphanumerics -> '_') and prefix a letter;
    it stays unique because the full ID is unique."""
    ident = re.sub(r"[^0-9A-Za-z]", "_", full_id)
    return "g_{}_{}".format(ident, i)


def _rungen_bin_name(full_id, i):
    return _emit_basename(full_id, i) + ".rungen"


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
    Raises HarnessError if the count isn't exactly one."""
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
        raise HarnessError(
            "expected exactly one registered generator (single-generator "
            "assumption), found {}: {}".format(len(names), names))
    return names[0]


# ---------------------------------------------------------------------------
# phase 2 (emit) + phase 4 (link) + run
# ---------------------------------------------------------------------------

def _emit(bin_dir, gen_exe, gen_name, basename, params, with_stmt):
    emits = ["static_library", "c_header", "registration"]
    if with_stmt:
        # Both the plain lowered loop nest and the conceptual (pre-lowering) form.
        emits += ["stmt", "conceptual_stmt"]
    cmd = (["./" + gen_exe, "-g", gen_name, "-o", ".", "-f", basename]
           + _param_tokens(params)
           + ["-e", ",".join(emits), "target=host-profile"])
    return _run_streamed(cmd, cwd=bin_dir)


def _link(bin_dir, basename):
    cmd = [_CXX, "-std=c++17", "-O2", _RUNGENMAIN_OBJ,
           basename + ".registration.cpp", basename + ".a",
           "-o", basename + ".rungen", "-lpthread", "-ldl"]
    return _run_streamed(cmd, cwd=bin_dir)


def _run_benchmark(bin_dir, rungen_bin, json_out_path, warnings_out_path):
    """Run one benchmark binary.  Captures the binary's stdout -- it is redirected
    into the benchmark sub-object (later read back by `view_benchmark_stdout`),
    NOT echoed to the harness stdout (idea.md Build Tool) -- while letting stderr
    flow.  Returns (rc, stdout_text)."""
    env = dict(os.environ)
    env["HL_PROFILER_JSON_OUTPUT"] = json_out_path
    # Andrew Adams's profiler doesn't put warnings in the main JSON yet; a
    # separate secret-menu env var names a file of per-pipeline warnings (see
    # reference_build_commands.md "Warnings Output").
    env["HL_PROFILER_JSON_TEMPORARY_WARNINGS"] = warnings_out_path
    # Deliberately NOT --quiet: RunGen's `halide_print:` profiler-stats table is
    # kept in the captured stdout.  It duplicates the parsed JSON, but the plain
    # table is an easier read than the JSON tools for simple tasks, and it lands
    # in the benchmark sub-object (viewable via `view_benchmark_stdout`) rather
    # than the harness output, so it isn't noise (idea.md Build Tool).
    cmd = ["./" + rungen_bin, "--benchmarks=all", "--estimate_all"]
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
    """Resolve --target: an explicit schedule ID, or the special `workspace`."""
    if spec != "workspace":
        return ctx.catalog.resolve_schedule(spec)
    unamb = ctx.unambiguous_schedule()
    if unamb is not None:
        return unamb
    idea = ctx.current_idea_node()
    if idea is None:
        raise DhHlError(
            "no unambiguous schedule node and no current idea node for the "
            "workspace; use `dh_hl set_idea <idea>` to pick an idea (then this "
            "adds a child schedule), or `dh_hl new_root` to start a new root")
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
    return ctx.catalog.resolve_schedule(spec)


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
    return ctx.catalog.resolve_schedule(spec)


def cmd_init_build(args):
    # init_build may CREATE the target node, so it needs the catalog lock (and
    # the session lock, since it writes the private workspace).
    ctx = Context.for_session(args, session_lock=True)
    ctx.workspace.ensure_private_dir()

    target = _resolve_target(ctx, getattr(args, "target", None) or "workspace")
    other = _resolve_other(ctx, getattr(args, "other", None) or "parent", target)
    anchor = _resolve_anchor(ctx, getattr(args, "anchor", None) or "auto")

    selection = {
        "target": _node_entry(ctx.catalog, "target", target),
        "other": _node_entry(ctx.catalog, "other", other) if other else None,
        "anchor": _node_entry(ctx.catalog, "anchor", anchor) if anchor else None,
    }
    # Persist BEFORE finish so the new target node is flushed first.
    path = os.path.join(ctx.workspace.private_dir, _INIT_BUILD_FILE)
    safety.write_allowed(path, json.dumps(selection, indent=1) + "\n")
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
            raise HarnessError(
                "schedule node {} has 0 generator parameters objects; nothing "
                "to build".format(self.full_id))
        self.cpp_ok = False
        # A harness/environment failure (RunGenMain.o, generator-count) is NOT a
        # build outcome to catalogue -> leave the node's result untouched.
        self.harness_error = False
        self.gen_name = None
        self.gen_ok = {}        # params index -> bool (emit succeeded)
        self.linked = {}        # params index -> bool (link succeeded)
        self.any_run_failed = False


def _load_selection(ws):
    path = os.path.join(ws.private_dir, _INIT_BUILD_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise DhHlError(
            "no init_build.json for this session; run `dh_hl init_build` first")


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
            raise HarnessError(
                "--only {} is out of range for target's {} parameters "
                "object(s)".format(only_index, len(target.params)))
        param_indices = {target.full_id: [only_index]}
    else:
        param_indices = {n.full_id: list(range(len(n.params))) for n in nodes}

    all_ok = _compile_phase(bin_dir, nodes, param_indices, target.full_id)

    # Phase 2: profiling (upgrade to exclusive machine lock first if profiling),
    # then the catalog lock, then find nodes + record.
    if profile_batches > 0:
        locks.upgrade_machine_exclusive()
    locks.acquire_catalog(catalog_dir)
    ctx = Context(Catalog(catalog_dir), session_id)
    catalog = ctx.catalog
    sched = {n.full_id: catalog.get_schedule(n.full_id) for n in nodes}

    bench_index = None
    if profile_batches > 0:
        bench_index, prof_ok = _profile_phase(
            bin_dir, nodes, param_indices, sched, catalog, profile_batches)
        all_ok = all_ok and prof_ok

    # Phase 3: result-state updates (monotone; never regress).  A harness error
    # (not a build outcome) leaves the node's result untouched.
    for n in nodes:
        if n.harness_error:
            continue
        result = _compute_result(n, param_indices[n.full_id], only_kind,
                                  profile_batches)
        node = sched[n.full_id]
        node.set_result(best_result(node.result, result))

    # Benchmark set: for an --only all OR --only target run that profiled >=1
    # batch with no subprocess failures (idea.md "Build Tool"): the dense index
    # is then guaranteed populated.  (--only <int> never makes a set.)
    if only_kind in ("all", "target") and profile_batches > 0 and all_ok:
        bs = catalog.create_benchmark_set(bench_index)
        # Add it to the session's private benchmark set list (idea.md Build Tool).
        ws.add_private_benchmark_set(bs.full_id, catalog)
        ctx.finish()
        print("dh_hl: Benchmark set ID: " + bs.full_id)
    else:
        ctx.finish()

    sys.exit(0 if all_ok else 1)


def _compile_phase(bin_dir, nodes, param_indices, target_id):
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
        except HarnessError as e:
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
            with_stmt = (n.full_id == target_id)
            basename = _emit_basename(n.full_id, i)
            print("dh_hl: begin Halide generator {}: {}".format(i, n.short_id))
            print("dh_hl: params={}".format(json.dumps(n.params[i])))
            _trace_build("emit", n.full_id, i)
            if _emit(bin_dir, _gen_exe_name(n.full_id), n.gen_name, basename,
                     n.params[i], with_stmt) != 0:
                n.gen_ok[i] = False
                all_ok = False
                print("dh_hl: end Halide generator {} fail".format(i))
                continue
            n.gen_ok[i] = True
            if with_stmt:
                _publish_stmt(bin_dir, basename, i)
            _trace_build("link", n.full_id, i)
            if _link(bin_dir, basename) != 0:
                n.linked[i] = False
                all_ok = False
                print("dh_hl: end Halide generator {} fail (link)".format(i))
                continue
            n.linked[i] = True
            print("dh_hl: end Halide generator {} success".format(i))
    return all_ok


def _publish_stmt(bin_dir, basename, i):
    """Copy the target's emitted .stmt / .conceptual.stmt to the short human-
    friendly `bin/{i}.stmt` names and announce them (idea.md pseudocode)."""
    for suffix in (".stmt", ".conceptual.stmt"):
        src = os.path.join(bin_dir, basename + suffix)
        if os.path.exists(src):
            dst = os.path.join(bin_dir, "{}{}".format(i, suffix))
            shutil.copyfile(src, dst)
            print("dh_hl: stmt: " + dst)


def _profile_phase(bin_dir, nodes, param_indices, sched, catalog, batches):
    """Run *batches* interleaved profiling passes over every linked binary,
    attaching a benchmark sub-object to each binary's source schedule node.
    Returns (bench_index, all_ok).  bench_index[full_id][i][batch] = benchmark
    full ID (dense iff no subprocess failed)."""
    hostname = ids.stable_hostname()
    file_hostname = ids.sanitize_component(hostname)

    # The list of runnable (node, i) binaries, and the empty dense index.
    binaries = []
    bench_index = {}
    for n in nodes:
        idxs = param_indices[n.full_id]
        bench_index[n.full_id] = [[None] * batches for _ in idxs]
        for slot, i in enumerate(idxs):
            if n.linked.get(i):
                binaries.append((n, i, slot))

    all_ok = True
    json_out = os.path.abspath(os.path.join(bin_dir, "profile_out.json"))
    warnings_out = os.path.abspath(os.path.join(bin_dir, "profile_warnings.json"))
    for batch in range(batches):
        random.shuffle(binaries)  # interleaved: fresh order each batch
        _trace_build("batch", batch)
        for n, i, slot in binaries:
            for p in (json_out, warnings_out):
                if os.path.exists(p):
                    os.remove(p)
            _trace_build("profile", n.full_id, i)
            rc, stdout_text = _run_benchmark(
                bin_dir, _rungen_bin_name(n.full_id, i), json_out, warnings_out)
            ok = rc == 0
            # The profile phase holds the catalog lock, so format the short ID
            # live from the resolved node (no need for the precomputed one).
            print("dh_hl: Profiled {}, binary {} ({})".format(
                catalog.format_schedule_id(sched[n.full_id]), i,
                "success" if ok else "fail"))
            if not ok:
                n.any_run_failed = True
                all_ok = False
                continue
            try:
                bench_obj = _build_benchmark_obj(
                    json_out, warnings_out, hostname, n.params[i],
                    stdout_text, catalog.fresh_timestamp())
            except HarnessError as e:
                print("dh_hl: skipping benchmark: " + str(e), file=sys.stderr)
                n.any_run_failed = True
                all_ok = False
                continue
            bench = sched[n.full_id].add_benchmark(file_hostname, bench_obj)
            bench_index[n.full_id][slot][batch] = bench.full_id
            print("dh_hl: Benchmark ID: " + catalog.format_benchmark_id(bench))
    return bench_index, all_ok


def _build_benchmark_obj(json_out, warnings_out, hostname, params, stdout_text,
                         timestamp):
    with open(json_out, "r", encoding="utf-8") as f:
        prof = json.load(f)
    pipelines = prof.get("pipelines")
    if not isinstance(pipelines, list) or len(pipelines) != 1:
        raise HarnessError(
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
        "profiler": pipelines[0],
        "warnings": profiler_warnings.warnings_from_temp_file(warnings_out),
        "stdout": stdout_text,
    }


def _compute_result(n, indices, only_kind, profile_batches):
    """The result state this run establishes for node *n* (idea.md pseudocode
    step 3).  best_result() at the call site keeps it monotone."""
    if not n.cpp_ok:
        return "c++ error"
    # --only [int] builds a single binary, so success is not provable.
    if only_kind == "index" or any(not n.gen_ok.get(i) for i in indices):
        return "halide error"
    if profile_batches == 0 or n.any_run_failed \
            or any(not n.linked.get(i) for i in indices):
        return "runtime error"
    return "success"
