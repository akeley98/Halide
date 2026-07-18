"""build/profile logic with all subprocess steps stubbed (no Halide).

Runs in-process (monkeypatch seams can't cross a subprocess boundary), driving
cmd_build / cmd_profile through `run_tool` so the per-command lock lifecycle is
modeled.  The `session` fixture's workspace is already consistent with its seed
idea's canonical schedule, so build/profile edit that node directly."""

import json
import os

import pytest

from dendritic_hl_lib import build, tools
from dendritic_hl_lib.errors import HarnessError


@pytest.fixture
def fake_build(monkeypatch):
    """Stub every external step so build/profile exercise pure logic.

    Returns a dict of knobs the test can flip (e.g. make phase 1 fail)."""
    knobs = {"gen_rc": 0, "rungenmain_rc": 0, "emit_rc": 0, "link_rc": 0,
             "bench_rc": 0, "gen_name": "dummy"}

    monkeypatch.setattr(build, "_write_ninja", lambda bin_dir, ws: "ninja.txt")

    def fake_ninja(bin_dir, ninja_path, targets):
        if build._GEN_EXE in targets:
            return knobs["gen_rc"]
        return knobs["rungenmain_rc"]
    monkeypatch.setattr(build, "_ninja_build", fake_ninja)

    def fake_discover(bin_dir):
        if knobs["gen_name"] is None:
            raise HarnessError("generator count != 1 (injected)")
        return knobs["gen_name"]
    monkeypatch.setattr(build, "_discover_generator_name", fake_discover)

    monkeypatch.setattr(build, "_emit", lambda *a, **k: knobs["emit_rc"])
    monkeypatch.setattr(build, "_link", lambda bin_dir: knobs["link_rc"])

    def fake_bench(bin_dir, json_out):
        with open(json_out, "w") as f:
            json.dump({"pipelines": [{"name": "dummy", "time_ns": 42}]}, f)
        return knobs["bench_rc"]
    monkeypatch.setattr(build, "_run_benchmark", fake_bench)
    return knobs


def _result(session, run_tool, capsys):
    capsys.readouterr()  # discard any buffered output from prior commands
    run_tool(tools.cmd_json_schedule_info, session.ns())
    return json.loads(capsys.readouterr().out)


def test_build_success(session, run_tool, fake_build, capsys):
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, session.ns())
    assert e.value.code == 0
    assert _result(session, run_tool, capsys)["result"] == "success"


def test_build_lock_order(session, run_tool, fake_build):
    """build takes the catalog lock only after compiling, and never upgrades the
    machine lock to exclusive (only profiling monopolizes the machine)."""
    from dendritic_hl_lib import locks
    with pytest.raises(SystemExit):
        run_tool(build.cmd_build, session.ns())
    assert locks._trace_sink == [
        ("machine", "shared"), ("session", "exclusive"), ("catalog", "exclusive")]


def test_profile_lock_order_upgrades_before_catalog(session, run_tool, fake_build):
    """profile upgrades the machine lock to exclusive BEFORE taking the catalog
    lock (per the lock hierarchy)."""
    from dendritic_hl_lib import locks
    with pytest.raises(SystemExit):
        run_tool(build.cmd_profile, session.ns())
    assert locks._trace_sink == [
        ("machine", "shared"), ("session", "exclusive"),
        ("machine", "exclusive"), ("catalog", "exclusive")]


def test_build_cpp_error_persists_node_exit_1(session, run_tool, fake_build,
                                              capsys):
    fake_build["gen_rc"] = 1  # phase 1 (C++ compile) fails
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, session.ns())
    assert e.value.code == 1
    assert _result(session, run_tool, capsys)["result"] == "c++ error"


def test_build_halide_error(session, run_tool, fake_build, capsys):
    fake_build["emit_rc"] = 1  # generator (phase 2) fails
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, session.ns())
    assert e.value.code == 1
    assert _result(session, run_tool, capsys)["result"] == "halide error"


def test_generator_count_harness_error_no_result_update(session, run_tool,
                                                        fake_build, capsys):
    fake_build["gen_name"] = None  # discovery raises HarnessError
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, session.ns())
    assert e.value.code == 1
    # Node persists (C++ compiled), but result stays at the default (no update).
    assert _result(session, run_tool, capsys)["result"] == "c++ error"


def test_profile_records_two_benchmarks(session, run_tool, fake_build, tmp_path,
                                        capsys):
    params = tmp_path / "params.json"
    params.write_text('[{"offset": 5}, {"offset": 20}]')
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_profile, session.ns(parameters=str(params)))
    assert e.value.code == 0
    obj = _result(session, run_tool, capsys)
    assert obj["result"] == "success"
    assert len(obj["benchmark"]) == 2
    assert [b["parameters"] for b in obj["benchmark"]] == [{"offset": 5},
                                                           {"offset": 20}]


def test_profile_json_path_is_absolute_with_relative_catalog(
        session, run_tool, fake_build, tmp_path, monkeypatch, capsys):
    """Regression: even with a RELATIVE -C, the profiler output path must be
    absolute, because it is handed to a child running with cwd=bin_dir."""
    seen = {}

    def spy_bench(bin_dir, json_out):
        seen["json_out"] = json_out
        with open(json_out, "w") as f:
            json.dump({"pipelines": [{"name": "x"}]}, f)
        return 0
    monkeypatch.setattr(build, "_run_benchmark", spy_bench)

    # Refer to the catalog by a relative path from its parent directory.
    monkeypatch.chdir(os.path.dirname(session.catalog_dir))
    rel_catalog = os.path.basename(session.catalog_dir)
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_profile,
                 session.ns(catalog=rel_catalog, parameters=None))
    assert e.value.code == 0
    assert os.path.isabs(seen["json_out"]), \
        "profiler JSON path must be absolute, got " + repr(seen["json_out"])


def test_profile_params_from_stdin(session, run_tool, fake_build, monkeypatch,
                                   capsys):
    """`-` reads the parameters JSON from stdin, like every other file input."""
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO('[{"offset": 1}, {"offset": 2}]'))
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_profile, session.ns(parameters="-"))
    assert e.value.code == 0
    obj = _result(session, run_tool, capsys)
    assert [b["parameters"] for b in obj["benchmark"]] == [{"offset": 1},
                                                           {"offset": 2}]


def test_emit_requests_both_stmt_forms(monkeypatch):
    """build (with_stmt=True) asks the generator for both the plain `stmt` and
    `conceptual_stmt`; profile (with_stmt=False) asks for neither."""
    seen = {}

    def spy(cmd, cwd=None, env=None):
        seen["cmd"] = cmd
        return 0
    monkeypatch.setattr(build, "_run_streamed", spy)

    build._emit("bin", "gen", {}, with_stmt=True)
    emits = seen["cmd"][seen["cmd"].index("-e") + 1].split(",")
    assert "stmt" in emits and "conceptual_stmt" in emits

    build._emit("bin", "gen", {}, with_stmt=False)
    emits = seen["cmd"][seen["cmd"].index("-e") + 1].split(",")
    assert "stmt" not in emits and "conceptual_stmt" not in emits


def test_build_prints_both_stmt_paths(session, run_tool, fake_build, capsys):
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, session.ns())
    assert e.value.code == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(ln.endswith(".stmt") and not ln.endswith(".conceptual.stmt")
               for ln in lines)
    assert any(ln.endswith(".conceptual.stmt") for ln in lines)


# ---- pure helper: parameter formatting ------------------------------------

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
