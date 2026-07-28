"""The `build` and `profile` tools: two-phase Halide build + benchmarking.

Per the decided build-driver split (impl.md Build Tool): ninja builds the
param-independent steps once (the C++ workspace -> generator executable, and
RunGenMain.o); Python drives the param-dependent steps serially with
subprocess (generator emit -> link -> optional benchmark run).

Failure handling follows impl.md Tool Safety Requirements: bad build *outcomes*
(`c++ error` / `halide error`) and the generator-count harness error are NOT
rollback-triggering exceptions; the node is still flushed/committed, and only
the process exit code reflects the failure.

TEST COUPLING: the module-level `_`-prefixed helpers that shell out to the
toolchain -- `_write_ninja`, `_ninja_build`, `_discover_generator_name`,
`_emit`, `_link`, `_run_benchmark` -- are monkeypatch seams. tests/test_build_fake.py
replaces them (by name, with matching signatures) so build/profile logic can be
exercised without a real Halide build, and `_emit`'s argv is inspected directly.
Treat their names and signatures as a lightly load-bearing test contract:
renaming, inlining, or re-signaturing one means updating that fixture in the
same change. See impl.md "Tests".
"""

import json
import os
import subprocess
import sys

from . import ids
from . import locks
from . import profiler_warnings
from . import safety
from .catalog import Catalog
from .context import Context, SessionWorkspace, resolve_target, read_text_or_stdin
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

_GEN_EXE = "dh_hl_generator"
_OUT_BASENAME = "dh_hl_gen"        # fixed, so emitted filenames are stable
_RUNGEN_BIN = _OUT_BASENAME + ".rungen"

_RESULT_RANK = {"c++ error": 0, "halide error": 1, "success": 2}

_CXX = "c++"


# ---------------------------------------------------------------------------
# subprocess helpers
# ---------------------------------------------------------------------------

def _run_streamed(cmd, cwd=None, env=None):
    """Run *cmd*, letting its stdout/stderr flow to ours.  Returns exit code."""
    print("+ " + " ".join(cmd), file=sys.stderr)
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


def _load_params_object(path):
    """A generator-parameters file for `build`: a single JSON object.
    "-" reads from stdin, like every other file input."""
    if path is None:
        return {}
    obj = json.loads(read_text_or_stdin(path))
    if not isinstance(obj, dict):
        raise DhHlError("parameters file must hold a JSON object")
    return obj


def _load_params_list(path):
    """A generator-parameters file for `profile`: [{}] default, [obj] for a
    single object, or the list verbatim.  "-" reads from stdin."""
    if path is None:
        return [{}]
    obj = json.loads(read_text_or_stdin(path))
    if isinstance(obj, dict):
        return [obj]
    if isinstance(obj, list):
        return obj
    raise DhHlError("parameters file must hold a JSON object or list")


# ---------------------------------------------------------------------------
# ninja: phase 1 (generator exe) + RunGenMain.o
# ---------------------------------------------------------------------------

def _write_ninja(bin_dir, workspace_path):
    """Write bin/build_ninja.txt building the two param-independent artifacts.
    Returns its path."""
    path = os.path.join(bin_dir, "build_ninja.txt")
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
        n.build(_GEN_EXE, "gen_exe", os.path.abspath(workspace_path))
        n.build("RunGenMain.o", "rungenmain_obj", _RUNGENMAIN_CPP)
    return path


def _ninja_build(bin_dir, ninja_path, targets):
    cmd = ["ninja", "-f", os.path.basename(ninja_path)] + targets
    return _run_streamed(cmd, cwd=bin_dir)


def _discover_generator_name(bin_dir):
    """Run the generator exe with no -g and scrape the single registered name.
    Raises HarnessError if the count isn't exactly one."""
    rc, out = _run_capture(["./" + _GEN_EXE], cwd=bin_dir)
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

def _emit(bin_dir, gen_name, params, with_stmt):
    emits = ["static_library", "c_header", "registration"]
    if with_stmt:
        # Both the plain lowered loop nest and the conceptual (pre-lowering) form.
        emits += ["stmt", "conceptual_stmt"]
    cmd = (["./" + _GEN_EXE, "-g", gen_name, "-o", ".", "-f", _OUT_BASENAME]
           + _param_tokens(params)
           + ["-e", ",".join(emits), "target=host-profile"])
    return _run_streamed(cmd, cwd=bin_dir)


def _link(bin_dir):
    cmd = [_CXX, "-std=c++17", "-O2", "RunGenMain.o",
           _OUT_BASENAME + ".registration.cpp", _OUT_BASENAME + ".a",
           "-o", _RUNGEN_BIN, "-lpthread", "-ldl"]
    return _run_streamed(cmd, cwd=bin_dir)


def _run_benchmark(bin_dir, json_out_path, warnings_out_path):
    env = dict(os.environ)
    env["HL_PROFILER_JSON_OUTPUT"] = json_out_path
    # Andrew Adams's profiler doesn't put warnings in the main JSON yet; a
    # separate secret-menu env var names a "JSON lines" file of per-pipeline
    # warnings (see reference_build_commands.md "Warnings Output").
    env["HL_PROFILER_JSON_TEMPORARY_WARNINGS"] = warnings_out_path
    cmd = ["./" + _RUNGEN_BIN, "--verbose", "--benchmarks=all", "--estimate_all"]
    return _run_streamed(cmd, cwd=bin_dir, env=env)


def _upgrade_result(node, new_result):
    if _RESULT_RANK[new_result] > _RESULT_RANK[node.result]:
        node.set_result(new_result)


# ---------------------------------------------------------------------------
# node selection (step 1, shared by build & profile)
# ---------------------------------------------------------------------------

def _snapshot_session(args):
    """Phase 1 setup: resolve the session, take the session lock, and ready the
    catalog-free private workspace.  Holds only the session + concurrent machine
    locks -- NOT the catalog lock -- so the expensive C++ compile that follows
    does not block other agents' catalog access (impl.md "Build/Profile Tools --
    Implementation Details").  Returns (catalog_dir, session_id, ws, bin_dir)."""
    catalog_dir, session_id = resolve_target(args)
    if session_id is None:
        raise DhHlError("this command requires a session (-s)")
    # Guard against a typo'd catalog dir before acquire_session would otherwise
    # create private/{id} (and the whole chain) under it.
    if not os.path.isdir(catalog_dir):
        raise DhHlError("no catalog directory: " + catalog_dir)
    locks.acquire_session(catalog_dir, session_id)
    ws = SessionWorkspace(catalog_dir, session_id)  # catalog-free (no lock yet)
    ws.ensure_private_dir()
    ws.require_workspace()
    bin_dir = ws.bin_dir
    os.makedirs(bin_dir, exist_ok=True)  # gitignored infra; created lazily
    return catalog_dir, session_id, ws, bin_dir


def _open_locked_context(catalog_dir, session_id):
    """Phase 2: acquire the catalog lock, then build the Context around the
    now-lockable Catalog (whose __init__ asserts the lock is held for it)."""
    locks.acquire_catalog(catalog_dir)
    return Context(Catalog(catalog_dir), session_id)


def _select_node(ctx):
    unamb = ctx.unambiguous_schedule()
    if unamb is not None:
        return unamb
    catalog = ctx.catalog
    cis = ctx.workspace.current_idea_state
    if cis.kind == "idea":
        idea = catalog.get_idea(cis.idea_id)
        return catalog.create_schedule(ctx.workspace.workspace_source,
                                       parent_idea=idea)
    raise DhHlError(
        "no unambiguous schedule node and no current idea node; use "
        "`dh_hl set_idea <idea>` to pick an idea, or `dh_hl new_root` to "
        "start a new root")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _compile_for_build(bin_dir, workspace_path, params):
    """Phase 1 of build: every subprocess compile step, under the session +
    concurrent machine locks only (no catalog lock).  Returns
    (outcome, stmt_paths, ok, harness_msg):

    * outcome: "c++ error" / "halide error" / "success", or None to leave the
      node's result untouched -- a harness/environment failure (generator-count,
      RunGenMain.o, link) is not a build outcome to catalogue.
    * stmt_paths: emitted .stmt paths to print (only on full success).
    * ok: whether the process should exit 0.
    * harness_msg: a message to print to stderr, or None.
    """
    ninja_path = _write_ninja(bin_dir, workspace_path)
    if _ninja_build(bin_dir, ninja_path, [_GEN_EXE]) != 0:
        return ("c++ error", [], False, None)
    try:
        # Generator-count check (after the C++ compiled): a workspace-authoring
        # problem, not a build outcome -> leave the result untouched.
        gen_name = _discover_generator_name(bin_dir)
    except HarnessError as e:
        return (None, [], False, str(e))
    if _ninja_build(bin_dir, ninja_path, ["RunGenMain.o"]) != 0:
        return (None, [], False, "failed to compile RunGenMain.o")
    if _emit(bin_dir, gen_name, params, with_stmt=True) != 0:
        return ("halide error", [], False, None)
    # The generator ran: the schedule is a success even if the harness then
    # fails to link the standalone binary.
    if _link(bin_dir) != 0:
        return ("success", [], False, "failed to link the standalone RunGen binary")
    stmt_paths = [os.path.join(bin_dir, _OUT_BASENAME + ".stmt"),
                  os.path.join(bin_dir, _OUT_BASENAME + ".conceptual.stmt")]
    return ("success", stmt_paths, True, None)


def cmd_build(args):
    # Phase 1: compile with only the session + concurrent machine locks held.
    catalog_dir, session_id, ws, bin_dir = _snapshot_session(args)
    params = _load_params_object(args.parameters)
    outcome, stmt_paths, ok, harness_msg = _compile_for_build(
        bin_dir, ws.workspace_path, params)

    # Phase 2: now take the catalog lock, find/create the node, record + finish.
    ctx = _open_locked_context(catalog_dir, session_id)
    node = _select_node(ctx)
    if outcome is not None:
        _upgrade_result(node, outcome)
    if harness_msg is not None:
        print("dh_hl: " + harness_msg, file=sys.stderr)
    _finish_and_exit(ctx, node, ok=ok, stmt_paths=stmt_paths)


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

def cmd_profile(args):
    # Phase 1: param-independent compile (generator exe + RunGenMain.o) under
    # only the session + concurrent machine locks.
    catalog_dir, session_id, ws, bin_dir = _snapshot_session(args)
    param_list = _load_params_list(args.parameters)

    ninja_path = _write_ninja(bin_dir, ws.workspace_path)
    gen_rc = _ninja_build(bin_dir, ninja_path, [_GEN_EXE])
    gen_name = None
    harness_msg = None
    if gen_rc == 0:
        try:
            gen_name = _discover_generator_name(bin_dir)
        except HarnessError as e:
            harness_msg = str(e)
        else:
            if _ninja_build(bin_dir, ninja_path, ["RunGenMain.o"]) != 0:
                gen_name = None
                harness_msg = "failed to compile RunGenMain.o"

    # Phase 2: monopolize the machine (upgrade to exclusive, BEFORE the catalog
    # lock per the lock hierarchy), then take the catalog lock + find/create node.
    locks.upgrade_machine_exclusive()
    ctx = _open_locked_context(catalog_dir, session_id)
    node = _select_node(ctx)

    if gen_rc != 0:
        _upgrade_result(node, "c++ error")
        _finish_and_exit(ctx, node, ok=False)
    if gen_name is None:
        # Harness error (generator-count / RunGenMain.o): leave result untouched.
        print("dh_hl: " + harness_msg, file=sys.stderr)
        _finish_and_exit(ctx, node, ok=False)

    # Phase 3: per-parameter emit -> link -> benchmark loop, machine held
    # exclusively and catalog lock held.  The stable hostname is de-anonymizing
    # and may contain spaces/punctuation (e.g. "David's MacBook Pro"); keep the
    # RAW value for the benchmark JSON field but use a SANITIZED form for the
    # benchmark file name.
    hostname = ids.stable_hostname()
    file_hostname = ids.sanitize_component(hostname)
    all_ok = True
    for params in param_list:
        if _emit(bin_dir, gen_name, params, with_stmt=False) != 0:
            _upgrade_result(node, "halide error")
            all_ok = False
            continue  # skip this parameter set, keep going
        _upgrade_result(node, "success")

        if _link(bin_dir) != 0:
            all_ok = False
            continue

        # MUST be absolute: they are handed to the benchmark child via env vars,
        # and the child runs with cwd=bin_dir, so a bin_dir-relative path would
        # be resolved against bin_dir twice.
        json_out = os.path.abspath(os.path.join(bin_dir, "profile_out.json"))
        warnings_out = os.path.abspath(
            os.path.join(bin_dir, "profile_warnings.json"))
        for p in (json_out, warnings_out):
            if os.path.exists(p):
                os.remove(p)
        if _run_benchmark(bin_dir, json_out, warnings_out) != 0:
            all_ok = False
            continue

        try:
            bench_obj = _build_benchmark_obj(json_out, warnings_out, hostname,
                                             params)
        except HarnessError as e:
            print("dh_hl: skipping parameter set: " + str(e), file=sys.stderr)
            all_ok = False
            continue
        bench = node.add_benchmark(file_hostname, bench_obj)
        # Print each benchmark's ID as it is saved (idea.md "Profile Tool").
        print("Benchmark ID: " + ctx.catalog.format_benchmark_id(bench))

    _finish_and_exit(ctx, node, ok=all_ok)


def _build_benchmark_obj(json_out, warnings_out, hostname, params):
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
        "parameters": params,
        "profiler": pipelines[0],
        "warnings": profiler_warnings.warnings_from_temp_file(warnings_out),
    }


# ---------------------------------------------------------------------------
# shared finish
# ---------------------------------------------------------------------------

def _finish_and_exit(ctx, node, ok, stmt_paths=None):
    """Flush the (possibly failed-outcome) node exactly once, print any emitted
    stmt paths and the node ID, then exit with a status reflecting *ok*."""
    for p in stmt_paths or ():
        print(p)
    ctx.finish()
    # Node ID is the last thing printed (after all subprocess output).
    print(ctx.catalog.format_schedule_id(node))
    sys.exit(0 if ok else 1)
