"""init_build/build logic with all subprocess steps stubbed (no Halide).

Runs in-process (monkeypatch seams can't cross a subprocess boundary), driving
cmd_init_build / cmd_build through `run_tool` so the per-command lock lifecycle
is modeled.  The `session` fixture's workspace is consistent with its seed
idea's canonical schedule, so `init_build --target workspace` selects that node
(no new node created) unless we perturb the workspace first.

Note the result semantics (idea.md Build Tool pseudocode step 3): `success`
means every Halide binary was BUILT (the generators emitted), so even a `build`
WITHOUT `--profile` reaches `success`; linking/running is not part of the result.
"""

import json
import os

import pytest

from dendritic_hl_lib import build, tools
from dendritic_hl_lib.errors import DhHlError, HalideBuildError
from conftest import branch_fresh_idea, open_catalog


@pytest.fixture
def fake_build(monkeypatch):
    """Stub every external toolchain step so init_build/build exercise pure
    logic.  Returns a dict of knobs the test can flip."""
    knobs = {"gen_rc": 0, "rungenmain_rc": 0, "emit_rc": 0, "link_rc": 0,
             "runtime_rc": 0, "shared_rc": 0, "bench_rc": 0, "gen_name": "dummy",
             "stdout": "", "warnings": None}

    monkeypatch.setattr(build, "_write_ninja",
                        lambda bin_dir, full_id, src, toolchain: "ninja.txt")

    def fake_ninja(bin_dir, ninja_path, targets):
        if build._RUNGENMAIN_OBJ in targets:
            return knobs["rungenmain_rc"]
        return knobs["gen_rc"]
    monkeypatch.setattr(build, "_ninja_build", fake_ninja)

    def fake_discover(bin_dir, gen_exe):
        if knobs["gen_name"] is None:
            raise HalideBuildError("generator count != 1 (injected)")
        return knobs["gen_name"]
    monkeypatch.setattr(build, "_discover_generator_name", fake_discover)

    def fake_emit(bin_dir, gen_exe, gen_name, out_subdir, params, with_stmt):
        # Materialize the .stmt outputs (in the per-(node,i) subdir, as the real
        # emit does) so _publish_stmt has something to copy.
        if with_stmt and knobs["emit_rc"] == 0:
            sub = os.path.join(bin_dir, out_subdir)
            os.makedirs(sub, exist_ok=True)
            for suffix in (".stmt", ".conceptual.stmt"):
                with open(os.path.join(sub, build._PIPELINE + suffix), "w") as f:
                    f.write("// " + suffix)
        return knobs["emit_rc"]
    monkeypatch.setattr(build, "_emit", fake_emit)
    monkeypatch.setattr(build, "_link", lambda bin_dir, out_subdir: knobs["link_rc"])
    monkeypatch.setattr(build, "_ensure_runtime",
                        lambda bin_dir, gen_exe: knobs["runtime_rc"])
    monkeypatch.setattr(build, "_link_shared",
                        lambda bin_dir, out_subdir: knobs["shared_rc"])

    def fake_bench(bin_dir, cmd, extra_env, json_out, warnings_out):
        if not knobs.get("emit_json", True):
            # A broken runner: exits 0 but writes no profiler JSON.
            return knobs["bench_rc"], knobs["stdout"]
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
    branch_fresh_idea(session)  # canonical-less idea so a new child can be created
    session.write_workspace("edited source\n")           # inconsistent workspace
    _os.remove(_os.path.join(session.private_dir, "generator_parameters.json"))
    with pytest.raises(DhHlError, match="generator_parameters.json"):
        run_tool(build.cmd_init_build,
                 session.ns(target="workspace", other="none", anchor="none"))


def test_init_build_workspace_refuses_when_idea_has_canonical(
        session, run_tool):
    """idea.md "Init-Build Tool": with an ambiguous workspace and a
    current idea that already has a canonical, init_build --target workspace
    refuses (rather than piling another child onto a decided idea) and gives the
    SAME advice as `canon` -- branch a new idea off the canonical."""
    # The seed idea has a canonical; perturb the workspace so `status` is
    # ambiguous, forcing the create-a-new-child path that the new rule blocks.
    session.write_workspace("edited source\n")
    # Golden lines name the blocking canonical's SHORT ID; derive it rather than
    # hard-wiring the hash (the hashing scheme may change).
    cat = open_catalog(session.catalog_dir)
    try:
        seed = cat.get_idea(cat.get_session(session.session_id).seed_idea_id)
        canon_short = cat.format_schedule_id(cat.schedules[seed.canonical])
    finally:
        from dendritic_hl_lib import locks
        locks._reset_for_tests()
    with pytest.raises(DhHlError) as e:
        run_tool(build.cmd_init_build,
                 session.ns(target="workspace", other="none", anchor="none"))
    msg = str(e.value)
    assert "already has a canonical schedule" in msg
    assert "dh_hl new_idea <name> <proposal file> {}".format(canon_short) in msg
    assert "dh_hl set_idea" in msg


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


def test_failed_init_build_clears_prior_selection(session, run_tool):
    """Footgun guard (idea.md Init-Build Tool): a failed init_build must not
    leave an earlier success's selection lying around for `build` to reuse."""
    from dendritic_hl_lib.errors import DhHlError
    _init(session, run_tool)                       # success -> writes selection
    sel = os.path.join(session.private_dir, "init_build.json")
    assert os.path.exists(sel)

    # A later init_build that fails (--anchor always with no current anchor)
    # clears the stale selection, even though a prior init_build succeeded.
    with pytest.raises(DhHlError):
        run_tool(build.cmd_init_build,
                 session.ns(target="workspace", other="none", anchor="always"))
    assert not os.path.exists(sel)

    # So build refuses instead of silently reusing the earlier selection.
    with pytest.raises(DhHlError, match="no successful init_build"):
        run_tool(build.cmd_build, session.ns(profile=0, only="all"))


def test_failed_init_build_isolated_between_sessions(session, run_tool, fake_build,
                                                     tmp_path):
    """The selection is a per-session private-workspace file: a failed
    init_build in one session must not disturb another session's selection."""
    from dendritic_hl_lib.errors import DhHlError
    from conftest import make_catalog_session, Sess
    # Session A (the fixture): a successful init_build.
    _init(session, run_tool)
    sel_a = os.path.join(session.private_dir, "init_build.json")
    assert os.path.exists(sel_a)

    # Session B: an independent catalog+session whose init_build fails.
    b_dir, b_id = make_catalog_session(str(tmp_path / "projB.dh_hl"))
    sess_b = Sess(b_dir, b_id)
    with pytest.raises(DhHlError):
        run_tool(build.cmd_init_build,
                 sess_b.ns(target="workspace", other="none", anchor="always"))

    # A's selection is untouched, and A's build still runs off it.
    assert os.path.exists(sel_a)
    assert _build(session, run_tool) == 0


# ---------------------------------------------------------------------------
# build: result states
# ---------------------------------------------------------------------------

def test_build_no_profile_reaches_success(session, run_tool, fake_build, capsys):
    """A clean build with no profiling exits 0 and reaches `success`: the
    generators emitted, which is all `success` requires (running is a per-problem
    benchmark fact, not a node result)."""
    _init(session, run_tool)
    assert _build(session, run_tool, profile=0) == 0
    assert _result(session, run_tool, capsys)["result"] == "success"


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
    fake_build["gen_name"] = None  # discovery raises HalideBuildError
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
    branch_fresh_idea(session)  # canonical-less idea so a new child can be created
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

    def spy_bench(bin_dir, cmd, extra_env, json_out, warnings_out):
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


# ---------------------------------------------------------------------------
# build: problem-driven profiling (2d)
# ---------------------------------------------------------------------------

def _set_lines(out):
    return [ln for ln in out.splitlines()
            if ln.startswith("dh_hl: Benchmark set ID: ")]


def test_benchmark_carries_problem_and_params_index(session, run_tool,
                                                    fake_build, capsys):
    """Each profiled benchmark records the problem (full ID) it ran with and its
    generator-parameters index (idea.md "Benchmark Sub-object State")."""
    run_tool(tools.cmd_problem_full_id, session.ns(problem="main"))
    main_id = capsys.readouterr().out.strip()
    _init(session, run_tool)
    assert _build(session, run_tool, profile=1) == 0
    bench = _result(session, run_tool, capsys)["benchmark"][0]
    assert bench["parameters_index"] == 0
    assert bench["problem"] == main_id


def test_one_benchmark_set_per_enabled_problem(session, run_tool, fake_build,
                                               capsys):
    """With two enabled problems, a full profiling run makes one set PER problem
    (each single-problem, so its cached problem is uniform)."""
    run_tool(tools.cmd_new_problem,
             session.ns(short_name="alt", argv=["<RunGenMain>", "--alt"]))
    run_tool(build.cmd_init_build,
             session.ns(target="workspace", other="none", anchor="none"))
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, session.ns(profile=1, only="all"))
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert len(_set_lines(out)) == 2                       # default + alt
    assert "problem problem.default" in out and "problem problem.alt" in out


def test_problem_flag_selects_subset(session, run_tool, fake_build, capsys):
    """--problem restricts profiling to the named problem(s): only that problem's
    set is made (not every enabled problem's)."""
    run_tool(tools.cmd_new_problem,
             session.ns(short_name="alt", argv=["<RunGenMain>", "--alt"]))
    run_tool(build.cmd_init_build,
             session.ns(target="workspace", other="none", anchor="none"))
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build,
                 session.ns(profile=1, only="all", problem=["problem.alt"]))
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert len(_set_lines(out)) == 1
    assert "problem problem.alt" in out
    assert "problem problem.default" not in out


def test_broken_runner_no_json_is_bad_outcome_not_crash(session, run_tool,
                                                        fake_build, capsys):
    """A runner that exits 0 but emits no profiler JSON is a CATALOGUED BAD
    OUTCOME (idea.md Build Tool): the profile loop skips that benchmark and keeps
    going -- nonzero exit, no benchmark set, NO traceback/rollback -- and the node
    still reaches `success` (the generators built)."""
    fake_build["emit_json"] = False
    _init(session, run_tool)
    capsys.readouterr()
    assert _build(session, run_tool, profile=1, only="all") == 1   # clean nonzero
    out = capsys.readouterr()
    assert "Benchmark set ID:" not in out.out
    assert "no profiler JSON" in out.err          # the skip message, not a crash
    # The node persisted with a success result (build succeeded; only the run did
    # not produce a benchmark).
    assert _result(session, run_tool, capsys)["result"] == "success"


def test_no_profile_when_compile_failed(session, run_tool, fake_build, capsys):
    """If a generator fails, profiling is skipped entirely (all-or-nothing on the
    build): no `Profiled` lines, no set, nonzero exit (idea.md Build Tool)."""
    fake_build["emit_rc"] = 1  # generator emit fails
    _init(session, run_tool)
    capsys.readouterr()
    assert _build(session, run_tool, profile=1, only="all") == 1
    out = capsys.readouterr().out
    assert "dh_hl: Profiled" not in out
    assert not _set_lines(out)


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


def test_copy_output_params_index():
    """The parameters-index resolution for copy_build_output (idea.md): None for
    `generator`, required when >1 params for other artifacts, default 0 for one,
    range-checked."""
    assert build._copy_output_params_index(3, "generator", None) is None
    with pytest.raises(DhHlError):
        build._copy_output_params_index(3, "stmt", None)      # required
    assert build._copy_output_params_index(1, "stmt", None) == 0   # default
    assert build._copy_output_params_index(3, "stmt", 2) == 2
    with pytest.raises(DhHlError):
        build._copy_output_params_index(2, "stmt", 5)         # out of range


def test_copy_build_output_stmt(session, run_tool, fake_build, capsys, tmp_path):
    """copy_build_output fetches an emitted artifact (the fake emit materializes
    the .stmt) from the session bin/ to a file."""
    _init(session, run_tool)
    _build(session, run_tool, profile=0)
    capsys.readouterr()
    dst = str(tmp_path / "out.stmt")
    run_tool(build.cmd_copy_build_output,
             session.ns(output=dst, what="stmt", schedule=None, parameters=None))
    assert open(dst, encoding="utf-8").read() == "// .stmt"   # fake_emit's marker


def test_copy_build_output_missing_is_clean_error(session, run_tool, fake_build,
                                                  capsys):
    """A `what` the build didn't produce (the fake emit makes no header) is a
    clean DhHlError, not a traceback."""
    _init(session, run_tool)
    _build(session, run_tool, profile=0)
    capsys.readouterr()
    with pytest.raises(DhHlError) as e:
        run_tool(build.cmd_copy_build_output,
                 session.ns(output="-", what="header", schedule=None,
                            parameters=None))
    assert "not found" in str(e.value)


def test_resolve_run_rungenmain():
    """A <RunGenMain> problem: argv[0] -> the absolute .rungen path, other tokens
    verbatim, no DENDRITIC_HL_OUTPUT_LIB (RunGenMain doesn't need it)."""
    cmd, env = build._resolve_run(
        "bin", ["<RunGenMain>", "--benchmarks=all", "--estimate_all"],
        "sub_0/dh_hl_pipeline.rungen", "sub_0/dh_hl_pipeline.dylib")
    assert os.path.isabs(cmd[0]) and cmd[0].endswith("sub_0/dh_hl_pipeline.rungen")
    assert cmd[1:] == ["--benchmarks=all", "--estimate_all"]
    assert env == {}


def test_resolve_run_custom_runner_with_lib():
    """A custom-runner problem: argv[0] stays the runner, <Lib> -> the absolute
    shared-library path, and the library is ALSO exported as DENDRITIC_HL_OUTPUT_LIB."""
    cmd, env = build._resolve_run(
        "bin", ["./runner", "<Lib>", "-v"],
        "sub_0/dh_hl_pipeline.rungen", "sub_0/dh_hl_pipeline.dylib")
    assert cmd[0] == "./runner"
    assert os.path.isabs(cmd[1]) and cmd[1].endswith("dh_hl_pipeline.dylib")
    assert cmd[2] == "-v"
    assert env["DENDRITIC_HL_OUTPUT_LIB"] == cmd[1]


def test_resolve_run_custom_runner_env_only():
    """A custom runner that omits <Lib> still receives the library via env."""
    cmd, env = build._resolve_run(
        "bin", ["./runner"], "sub_0/x.rungen", "sub_0/dh_hl_pipeline.dylib")
    assert cmd == ["./runner"]
    assert os.path.isabs(env["DENDRITIC_HL_OUTPUT_LIB"])
    assert env["DENDRITIC_HL_OUTPUT_LIB"].endswith("dh_hl_pipeline.dylib")


def test_emit_requests_both_stmt_forms(monkeypatch, tmp_path):
    """build (with_stmt=True) asks the generator for both `stmt` and
    `conceptual_stmt`; without, neither.  Also pins the stable-symbol layout:
    `-f dh_hl_pipeline` and `-o {subdir}`."""
    seen = {}

    def spy(cmd, cwd=None, env=None):
        seen["cmd"] = cmd
        return 0
    monkeypatch.setattr(build, "_run_streamed", spy)

    bin_dir = str(tmp_path)
    build._emit(bin_dir, "gen_exe", "gen", "sub_0", {}, with_stmt=True)
    cmd = seen["cmd"]
    assert cmd[cmd.index("-f") + 1] == build._PIPELINE   # stable symbol
    assert cmd[cmd.index("-o") + 1] == "sub_0"           # per-(node,i) subdir
    emits = cmd[cmd.index("-e") + 1].split(",")
    assert "stmt" in emits and "conceptual_stmt" in emits

    build._emit(bin_dir, "gen_exe", "gen", "sub_0", {}, with_stmt=False)
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


# ---------------------------------------------------------------------------
# Halide path (set/get + build prerequisite + anti-hardwiring guard)
# ---------------------------------------------------------------------------

def test_set_get_halide_path_roundtrip(session, run_tool, capsys):
    """set_halide_path stores a value; get_halide_path reads it back verbatim.
    The path is NOT validated, so a nonexistent directory is accepted."""
    run_tool(tools.cmd_set_halide_path, session.ns(path="/opt/some/halide_dir"))
    capsys.readouterr()
    run_tool(tools.cmd_get_halide_path, session.ns())
    assert capsys.readouterr().out.strip() == "/opt/some/halide_dir"


def test_build_requires_halide_path_with_advice(session, run_tool):
    """build fails cleanly (advising set_halide_path) when no Halide path is set,
    even after a successful init_build."""
    _init(session, run_tool)
    os.remove(os.path.join(session.private_dir, "halide_path.txt"))
    with pytest.raises(DhHlError, match="set_halide_path"):
        run_tool(build.cmd_build, session.ns(profile=0, only="all"))


def test_ninja_has_no_hardwired_halide_path(tmp_path):
    """The anti-hardwiring guard: the ninja file `build` writes derives EVERY
    Halide path from the session's Halide path (via `_Toolchain`).  With a Halide
    path that lacks the `/halide/` sub-string, the emitted ninja must contain no
    `/halide/` either -- catching any future hard-wired path leaking through."""
    halide_dir = str(tmp_path / "toolkit")   # deliberately no '/halide/'
    src = tmp_path / "generator.cpp"
    src.write_text("// generator\n", encoding="utf-8")
    bin_dir = str(tmp_path / "bin")
    os.makedirs(bin_dir)
    ninja_path = build._write_ninja(bin_dir, "sch_deadbeef", str(src),
                                    build._Toolchain(halide_dir))
    text = open(ninja_path, encoding="utf-8").read()
    assert "/halide/" not in text.lower()
    # ...and it really did use the given Halide dir (guarding a no-op test).
    assert os.path.join(halide_dir, "build", "include") in text
