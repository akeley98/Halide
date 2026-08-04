"""init_build/build logic with all subprocess steps stubbed (no Halide).

Runs in-process (monkeypatch seams can't cross a subprocess boundary), driving
cmd_init_build / cmd_build through `run_tool` so the per-command lock lifecycle
is modeled.  The `session` fixture's workspace is consistent with its seed
idea's canonical schedule, so `init_build --target workspace` selects that node
(no new node created) unless we perturb the workspace first.

Note the new result semantics: a `build` WITHOUT `--profile` caps at
`runtime error` (no binary is known to run); `--profile N` (all-ok) reaches
`success` (idea.md Build Tool pseudocode step 3).
"""

import json
import os

import pytest

from dendritic_hl_lib import build, tools
from dendritic_hl_lib.errors import HarnessError


@pytest.fixture
def fake_build(monkeypatch):
    """Stub every external toolchain step so init_build/build exercise pure
    logic.  Returns a dict of knobs the test can flip."""
    knobs = {"gen_rc": 0, "rungenmain_rc": 0, "emit_rc": 0, "link_rc": 0,
             "bench_rc": 0, "gen_name": "dummy", "stdout": "", "warnings": None}

    monkeypatch.setattr(build, "_write_ninja",
                        lambda bin_dir, full_id, src: "ninja.txt")

    def fake_ninja(bin_dir, ninja_path, targets):
        if build._RUNGENMAIN_OBJ in targets:
            return knobs["rungenmain_rc"]
        return knobs["gen_rc"]
    monkeypatch.setattr(build, "_ninja_build", fake_ninja)

    def fake_discover(bin_dir, gen_exe):
        if knobs["gen_name"] is None:
            raise HarnessError("generator count != 1 (injected)")
        return knobs["gen_name"]
    monkeypatch.setattr(build, "_discover_generator_name", fake_discover)

    def fake_emit(bin_dir, gen_exe, gen_name, basename, params, with_stmt):
        # Materialize the .stmt outputs so _publish_stmt has something to copy.
        if with_stmt and knobs["emit_rc"] == 0:
            for suffix in (".stmt", ".conceptual.stmt"):
                os.makedirs(bin_dir, exist_ok=True)
                with open(os.path.join(bin_dir, basename + suffix), "w") as f:
                    f.write("// " + suffix)
        return knobs["emit_rc"]
    monkeypatch.setattr(build, "_emit", fake_emit)
    monkeypatch.setattr(build, "_link", lambda bin_dir, basename: knobs["link_rc"])

    def fake_bench(bin_dir, rungen_bin, json_out, warnings_out):
        with open(json_out, "w") as f:
            # Include the fields the cost cache reads (wall_time_min,
            # profiler_version); the same dummy value for every binary is fine
            # here -- profiler-stat *attribution* is a Halide-tier concern.
            json.dump({"pipelines": [{"name": "dummy", "time_ns": 42,
                                      "profiler_version": 1,
                                      "wall_time_min": 42, "funcs": []}]}, f)
        if knobs["warnings"] is not None:
            with open(warnings_out, "w") as f:
                json.dump({"pipeline": "dummy", "warnings": knobs["warnings"]}, f)
        return knobs["bench_rc"], knobs["stdout"]
    monkeypatch.setattr(build, "_run_benchmark", fake_bench)
    return knobs


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _init(session, run_tool, target="workspace", other="none", anchor="none"):
    """Run init_build (default: target only) and return its stdout."""
    run_tool(build.cmd_init_build,
             session.ns(target=target, other=other, anchor=anchor))


def _build(session, run_tool, profile=0, only="all"):
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, session.ns(profile=profile, only=only))
    return e.value.code


def _result(session, run_tool, capsys):
    capsys.readouterr()  # discard buffered output from prior commands
    run_tool(tools.cmd_json_schedule_info, session.ns(schedule=None))
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# init_build
# ---------------------------------------------------------------------------

def test_init_build_writes_selection(session, run_tool, capsys):
    _init(session, run_tool)
    out = capsys.readouterr().out
    assert "dh_hl: init_build target:" in out
    assert "dh_hl: init_build other: (disabled)" in out
    assert "dh_hl: init_build anchor: (disabled)" in out
    sel = json.loads(open(os.path.join(session.private_dir,
                                       "init_build.json")).read())
    assert sel["target"]["role"] == "target"
    assert sel["other"] is None and sel["anchor"] is None
    # Paths are catalog-relative and point at the target node's files.
    assert sel["target"]["source"].startswith("sch/")
    assert sel["target"]["parameters"].endswith("generator_parameters.json")


def test_init_build_default_other_is_parent(session, run_tool, capsys):
    """--other parent (default) resolves to the target's parent idea's parent
    schedule -- here the root, since the workspace node is the seed canonical."""
    run_tool(build.cmd_init_build,
             session.ns(target="workspace", other="parent", anchor="none"))
    sel = json.loads(open(os.path.join(session.private_dir,
                                       "init_build.json")).read())
    assert sel["other"] is not None and sel["other"]["role"] == "other"


def test_build_without_init_build_errors(session, run_tool):
    from dendritic_hl_lib.errors import DhHlError
    with pytest.raises(DhHlError):
        run_tool(build.cmd_build, session.ns(profile=0, only="all"))


def test_init_build_workspace_missing_params_errors(session, run_tool):
    """--target workspace requires generator_parameters.json (no implicit [{}]);
    a workspace with only generator.cpp is a clean error."""
    import os as _os
    from dendritic_hl_lib.errors import DhHlError
    session.write_workspace("edited source\n")           # inconsistent workspace
    _os.remove(_os.path.join(session.private_dir, "generator_parameters.json"))
    with pytest.raises(DhHlError, match="generator_parameters.json"):
        run_tool(build.cmd_init_build,
                 session.ns(target="workspace", other="none", anchor="none"))


def test_init_build_anchor_always_errors_without_current_anchor(session, run_tool):
    from dendritic_hl_lib.errors import DhHlError
    with pytest.raises(DhHlError, match="no current anchor"):
        run_tool(build.cmd_init_build,
                 session.ns(target="workspace", other="none", anchor="always"))


def test_init_build_anchor_auto_uses_current_anchor(session, run_tool, capsys):
    # No current anchor yet -> auto disables it.
    run_tool(build.cmd_init_build,
             session.ns(target="workspace", other="none", anchor="auto"))
    sel = json.loads(open(os.path.join(session.private_dir,
                                       "init_build.json")).read())
    assert sel["anchor"] is None
    # Set a current anchor, then auto picks it up.
    run_tool(tools.cmd_set_current_anchor, session.ns(schedule=None))
    run_tool(build.cmd_init_build,
             session.ns(target="workspace", other="none", anchor="auto"))
    sel = json.loads(open(os.path.join(session.private_dir,
                                       "init_build.json")).read())
    assert sel["anchor"] is not None and sel["anchor"]["role"] == "anchor"


# ---------------------------------------------------------------------------
# build: result states
# ---------------------------------------------------------------------------

def test_build_no_profile_is_runtime_error(session, run_tool, fake_build, capsys):
    """A clean build with no profiling exits 0 (all subprocesses succeeded) but
    the result state caps at `runtime error` -- no binary is known to run."""
    _init(session, run_tool)
    assert _build(session, run_tool, profile=0) == 0
    assert _result(session, run_tool, capsys)["result"] == "runtime error"


def test_build_profile_success(session, run_tool, fake_build, capsys):
    _init(session, run_tool)
    assert _build(session, run_tool, profile=1) == 0
    assert _result(session, run_tool, capsys)["result"] == "success"


def test_build_cpp_error_persists_node_exit_1(session, run_tool, fake_build,
                                              capsys):
    _init(session, run_tool)
    fake_build["gen_rc"] = 1  # generator-exe C++ compile fails
    assert _build(session, run_tool, profile=0) == 1
    assert _result(session, run_tool, capsys)["result"] == "c++ error"


def test_build_halide_error(session, run_tool, fake_build, capsys):
    _init(session, run_tool)
    fake_build["emit_rc"] = 1  # generator (emit) fails
    assert _build(session, run_tool, profile=0) == 1
    assert _result(session, run_tool, capsys)["result"] == "halide error"


def test_generator_count_harness_error_no_result_update(session, run_tool,
                                                        fake_build, capsys):
    _init(session, run_tool)
    fake_build["gen_name"] = None  # discovery raises HarnessError
    assert _build(session, run_tool, profile=0) == 1
    # Node persists but result stays at the default (harness error, no update).
    assert _result(session, run_tool, capsys)["result"] == "unknown"


def test_only_index_caps_at_halide_error(session, run_tool, fake_build, capsys):
    """--only N builds a single binary, so success is not provable even with
    profiling: the result caps at halide error."""
    _init(session, run_tool)
    assert _build(session, run_tool, profile=1, only="0") == 0
    assert _result(session, run_tool, capsys)["result"] == "halide error"


# ---------------------------------------------------------------------------
# build: lock order
# ---------------------------------------------------------------------------

def _locks_only(sink):
    """The lock events from the shared trace sink (build command events, which
    start with "build", are filtered out)."""
    return [e for e in sink if e[0] != "build"]


def test_build_lock_order_no_profile(session, run_tool, fake_build):
    """build takes the catalog lock after compiling and never upgrades the
    machine lock (only profiling monopolizes the machine)."""
    from dendritic_hl_lib import locks
    _init(session, run_tool)
    _build(session, run_tool, profile=0)
    assert _locks_only(locks._trace_sink) == [
        ("machine", "shared"), ("session", "exclusive"), ("catalog", "exclusive")]


def test_build_profile_lock_order_upgrades_before_catalog(session, run_tool,
                                                          fake_build):
    from dendritic_hl_lib import locks
    _init(session, run_tool)
    _build(session, run_tool, profile=1)
    assert _locks_only(locks._trace_sink) == [
        ("machine", "shared"), ("session", "exclusive"),
        ("machine", "exclusive"), ("catalog", "exclusive")]


# ---------------------------------------------------------------------------
# build: benchmarks
# ---------------------------------------------------------------------------

def test_build_profiles_each_parameter_set(session, run_tool, fake_build,
                                           capsys):
    """A target with two parameters objects yields two benchmarks (one binary
    each) per batch.  init_build --target workspace picks up the perturbed
    workspace params by creating a new child schedule."""
    session.write_params('[{"offset": 5}, {"offset": 20}]')
    _init(session, run_tool)  # workspace now inconsistent -> new child node
    assert _build(session, run_tool, profile=1) == 0
    obj = _result(session, run_tool, capsys)
    assert obj["result"] == "success"
    assert len(obj["benchmark"]) == 2
    assert sorted(b["parameters"]["offset"] for b in obj["benchmark"]) == [5, 20]


def test_benchmark_records_timestamp_and_stdout(session, run_tool, fake_build,
                                                capsys):
    fake_build["stdout"] = "hello from the binary\n"
    _init(session, run_tool)
    assert _build(session, run_tool, profile=1) == 0
    obj = _result(session, run_tool, capsys)
    bench = obj["benchmark"][0]
    assert bench["stdout"] == "hello from the binary\n"
    assert "timestamp" in bench and bench["timestamp"]


def test_profile_json_path_is_absolute_with_relative_catalog(
        session, run_tool, fake_build, monkeypatch):
    """Regression: with a RELATIVE -C, the profiler output path must still be
    absolute (it is handed to a child running with cwd=bin_dir)."""
    seen = {}

    def spy_bench(bin_dir, rungen_bin, json_out, warnings_out):
        seen["json_out"] = json_out
        with open(json_out, "w") as f:
            json.dump({"pipelines": [{"name": "x", "profiler_version": 1,
                                      "wall_time_min": 42, "funcs": []}]}, f)
        return 0, ""
    monkeypatch.setattr(build, "_run_benchmark", spy_bench)

    _init(session, run_tool)
    monkeypatch.chdir(os.path.dirname(session.catalog_dir))
    rel_catalog = os.path.basename(session.catalog_dir)
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build,
                 session.ns(catalog=rel_catalog, profile=1, only="all"))
    assert e.value.code == 0
    assert os.path.isabs(seen["json_out"]), \
        "profiler JSON path must be absolute, got " + repr(seen["json_out"])


def test_build_prints_stmt_paths(session, run_tool, fake_build, capsys):
    _init(session, run_tool)
    _build(session, run_tool, profile=0)
    lines = capsys.readouterr().out.splitlines()
    assert any(ln.startswith("dh_hl: stmt:") and ln.endswith("0.stmt")
               and not ln.endswith(".conceptual.stmt") for ln in lines)
    assert any(ln.startswith("dh_hl: stmt:") and ln.endswith("0.conceptual.stmt")
               for ln in lines)


# ---------------------------------------------------------------------------
# build: benchmark set (only for --only all --profile >=1, all ok)
# ---------------------------------------------------------------------------

def test_benchmark_set_created_for_full_run(session, run_tool, fake_build,
                                            capsys):
    """A full --only all --profile run over target+other produces a benchmark
    set indexed [schedule][params index][batch]."""
    run_tool(build.cmd_init_build,
             session.ns(target="workspace", other="parent", anchor="none"))
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, session.ns(profile=2, only="all"))
    assert e.value.code == 0
    out = capsys.readouterr().out
    set_line = [ln for ln in out.splitlines()
                if ln.startswith("dh_hl: Benchmark set ID: ")]
    assert set_line, "expected a benchmark set to be created"
    bs_id = set_line[0].split("dh_hl: Benchmark set ID: ", 1)[1].strip()

    run_tool(tools.cmd_json_benchmark_set_info, session.ns(benchmark_set=bs_id))
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 2  # target + other schedule nodes
    for sched_id, per_param in data.items():
        assert len(per_param) == 1        # each node has 1 params object
        assert len(per_param[0]) == 2     # 2 batches
        assert all(isinstance(b, str) for b in per_param[0])

    # The generated set is recorded in the session's private benchmark set list.
    import json as _json
    priv = _json.load(open(os.path.join(session.private_dir,
                                        "private_benchmark_sets.json")))
    assert bs_id in priv


def test_benchmark_set_created_for_only_target(session, run_tool, fake_build,
                                               capsys):
    """--only target --profile also produces a benchmark set (idea.md), holding
    just the target node."""
    run_tool(build.cmd_init_build,
             session.ns(target="workspace", other="parent", anchor="none"))
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, session.ns(profile=2, only="target"))
    assert e.value.code == 0
    out = capsys.readouterr().out
    set_line = [ln for ln in out.splitlines()
                if ln.startswith("dh_hl: Benchmark set ID: ")]
    assert set_line, "expected a benchmark set for --only target"
    bs_id = set_line[0].split("dh_hl: Benchmark set ID: ", 1)[1].strip()
    run_tool(tools.cmd_json_benchmark_set_info, session.ns(benchmark_set=bs_id))
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1  # target node only (other/anchor not built)


def test_no_benchmark_set_without_profile(session, run_tool, fake_build, capsys):
    run_tool(build.cmd_init_build,
             session.ns(target="workspace", other="parent", anchor="none"))
    capsys.readouterr()
    _build(session, run_tool, profile=0, only="all")
    assert "Benchmark set ID:" not in capsys.readouterr().out


def test_no_benchmark_set_for_only_index(session, run_tool, fake_build, capsys):
    """--only <int> profiles a single binary but never makes a benchmark set
    (idea.md): only 'all'/'target' do."""
    _init(session, run_tool)
    capsys.readouterr()
    assert _build(session, run_tool, profile=1, only="0") == 0
    assert "Benchmark set ID:" not in capsys.readouterr().out


def test_no_benchmark_set_when_a_subprocess_fails(session, run_tool, fake_build,
                                                  capsys):
    """A failed profiling subprocess (all_ok False) suppresses the benchmark set
    even for --only all --profile."""
    fake_build["bench_rc"] = 1  # every benchmark run "fails"
    run_tool(build.cmd_init_build,
             session.ns(target="workspace", other="parent", anchor="none"))
    capsys.readouterr()
    assert _build(session, run_tool, profile=1, only="all") == 1  # nonzero exit
    assert "Benchmark set ID:" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (True, "true"),
    (False, "false"),
    (10, "10"),
    (3.0, "3"),       # whole float -> %d
    (0.5, "0.5"),     # non-whole -> full-precision repr
    ("host", "host"),
])
def test_format_param_value(value, expected):
    assert build._format_param_value(value) == expected


def test_emit_requests_both_stmt_forms(monkeypatch):
    """build (with_stmt=True) asks the generator for both `stmt` and
    `conceptual_stmt`; without, neither."""
    seen = {}

    def spy(cmd, cwd=None, env=None):
        seen["cmd"] = cmd
        return 0
    monkeypatch.setattr(build, "_run_streamed", spy)

    build._emit("bin", "gen_exe", "gen", "base", {}, with_stmt=True)
    emits = seen["cmd"][seen["cmd"].index("-e") + 1].split(",")
    assert "stmt" in emits and "conceptual_stmt" in emits

    build._emit("bin", "gen_exe", "gen", "base", {}, with_stmt=False)
    emits = seen["cmd"][seen["cmd"].index("-e") + 1].split(",")
    assert "stmt" not in emits and "conceptual_stmt" not in emits


# ---- profiler warnings piping ---------------------------------------------

_WARNINGS = [
    {"rule": "no_vector_ops", "func": "hist_rows",
     "message": "hist_rows not vectorized", "canonical_id": 3},
]


def test_profile_pipes_warnings(session, run_tool, fake_build, capsys):
    fake_build["warnings"] = _WARNINGS
    _init(session, run_tool)
    assert _build(session, run_tool, profile=1) == 0
    obj = _result(session, run_tool, capsys)
    assert obj["benchmark"][0]["warnings"] == _WARNINGS


def test_profile_no_warnings_file_is_empty_list(session, run_tool, fake_build,
                                                capsys):
    _init(session, run_tool)  # knobs["warnings"] stays None -> no file
    assert _build(session, run_tool, profile=1) == 0
    obj = _result(session, run_tool, capsys)
    assert obj["benchmark"][0]["warnings"] == []
