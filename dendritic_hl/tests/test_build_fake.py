"""build/profile logic with all subprocess steps stubbed (no Halide)."""

import json
import os

import pytest

from dendritic_hl_lib import build, tools
from dendritic_hl_lib.errors import HarnessError
from conftest import ns


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

    monkeypatch.setattr(build, "_emit",
                        lambda *a, **k: knobs["emit_rc"])
    monkeypatch.setattr(build, "_link", lambda bin_dir: knobs["link_rc"])

    def fake_bench(bin_dir, json_out):
        with open(json_out, "w") as f:
            json.dump({"pipelines": [{"name": "dummy", "time_ns": 42}]}, f)
        return knobs["bench_rc"]
    monkeypatch.setattr(build, "_run_benchmark", fake_bench)
    return knobs


def _result(workspace, capsys):
    capsys.readouterr()  # discard any buffered output from prior commands
    tools.cmd_json_schedule_info(ns(workspace=str(workspace)))
    return json.loads(capsys.readouterr().out)


def test_build_success(workspace, fake_build, capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        build.cmd_build(ns(workspace=str(workspace)))
    assert e.value.code == 0
    assert _result(workspace, capsys)["result"] == "success"


def test_build_cpp_error_persists_node_exit_1(workspace, fake_build, capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    capsys.readouterr()
    fake_build["gen_rc"] = 1  # phase 1 (C++ compile) fails
    with pytest.raises(SystemExit) as e:
        build.cmd_build(ns(workspace=str(workspace)))
    assert e.value.code == 1
    assert _result(workspace, capsys)["result"] == "c++ error"  # node persists


def test_build_halide_error(workspace, fake_build, capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    capsys.readouterr()
    fake_build["emit_rc"] = 1  # generator (phase 2) fails
    with pytest.raises(SystemExit) as e:
        build.cmd_build(ns(workspace=str(workspace)))
    assert e.value.code == 1
    assert _result(workspace, capsys)["result"] == "halide error"


def test_generator_count_harness_error_no_result_update(workspace, fake_build,
                                                        capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    capsys.readouterr()
    fake_build["gen_name"] = None  # discovery raises HarnessError
    with pytest.raises(SystemExit) as e:
        build.cmd_build(ns(workspace=str(workspace)))
    assert e.value.code == 1
    # Node persists (C++ compiled), but result stays at the default (no update).
    assert _result(workspace, capsys)["result"] == "c++ error"


def test_profile_records_two_benchmarks(workspace, fake_build, tmp_path, capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    capsys.readouterr()
    params = tmp_path / "params.json"
    params.write_text('[{"offset": 5}, {"offset": 20}]')
    with pytest.raises(SystemExit) as e:
        build.cmd_profile(ns(workspace=str(workspace), parameters=str(params)))
    assert e.value.code == 0
    obj = _result(workspace, capsys)
    assert obj["result"] == "success"
    assert len(obj["benchmark"]) == 2
    assert [b["parameters"] for b in obj["benchmark"]] == [{"offset": 5},
                                                           {"offset": 20}]


def test_profile_json_path_is_absolute_with_relative_workspace(
        workspace, fake_build, tmp_path, monkeypatch, capsys):
    """Regression: with a RELATIVE workspace arg (the normal CLI case), the
    profiler output path must be absolute, because it is handed to a child that
    runs with cwd=bin_dir -- a bin_dir-relative path gets resolved twice and
    the JSON is written nowhere we can read it."""
    monkeypatch.chdir(tmp_path)  # so "gen.cpp" is a relative workspace path

    seen = {}

    def spy_bench(bin_dir, json_out):
        seen["json_out"] = json_out
        with open(json_out, "w") as f:
            json.dump({"pipelines": [{"name": "x"}]}, f)
        return 0
    monkeypatch.setattr(build, "_run_benchmark", spy_bench)  # overrides fake

    tools.cmd_new_root(ns(workspace="gen.cpp"))
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        build.cmd_profile(ns(workspace="gen.cpp", parameters=None))
    assert e.value.code == 0
    assert os.path.isabs(seen["json_out"]), \
        "profiler JSON path must be absolute, got " + repr(seen["json_out"])


def test_profile_params_from_stdin(workspace, fake_build, monkeypatch, capsys):
    """`-` reads the parameters JSON from stdin, like every other file input."""
    import io
    tools.cmd_new_root(ns(workspace=str(workspace)))
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO('[{"offset": 1}, {"offset": 2}]'))
    with pytest.raises(SystemExit) as e:
        build.cmd_profile(ns(workspace=str(workspace), parameters="-"))
    assert e.value.code == 0
    obj = _result(workspace, capsys)
    assert [b["parameters"] for b in obj["benchmark"]] == [{"offset": 1},
                                                           {"offset": 2}]


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
