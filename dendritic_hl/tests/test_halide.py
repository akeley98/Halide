"""Opt-in end-to-end test against the real local Halide build.

Skipped unless ~/Halide/build exists AND ninja is available.  Run explicitly:

    .venv/bin/python -m pytest tests/test_halide.py -v

Uses the real brighten generator so build/profile go through the actual
two-phase build, generator-name discovery, and profiler.
"""

import json
import os
import shutil

import pytest

from dendritic_hl_lib import build, tools
from conftest import ns, _PKG_ROOT

_BRIGHTEN = os.path.join(_PKG_ROOT, "rungen_example", "brighten_generator.cpp")

pytestmark = [
    pytest.mark.halide,
    pytest.mark.skipif(not os.path.isdir(build.HALIDE_BUILD),
                       reason="no local Halide build at " + build.HALIDE_BUILD),
    pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not found"),
    pytest.mark.skipif(not os.path.isfile(_BRIGHTEN),
                       reason="brighten example generator missing"),
]


@pytest.fixture
def brighten_ws(tmp_path, reset_safety):
    ws = tmp_path / "gen.cpp"
    shutil.copyfile(_BRIGHTEN, ws)
    return ws


def test_build_and_profile_real_halide(brighten_ws, tmp_path, capsys):
    tools.cmd_new_root(ns(workspace=str(brighten_ws)))
    capsys.readouterr()

    with pytest.raises(SystemExit) as e:
        build.cmd_build(ns(workspace=str(brighten_ws)))
    assert e.value.code == 0
    # build prints the conceptual.stmt path; it should really exist.
    out = capsys.readouterr().out.splitlines()
    stmt = out[0]
    assert stmt.endswith(".conceptual.stmt") and os.path.isfile(stmt)

    params = tmp_path / "p.json"
    params.write_text('[{"offset": 5}, {"offset": 30}]')
    with pytest.raises(SystemExit) as e:
        build.cmd_profile(ns(workspace=str(brighten_ws), parameters=str(params)))
    assert e.value.code == 0

    capsys.readouterr()  # discard profile output before reading the JSON
    tools.cmd_json_schedule_info(ns(workspace=str(brighten_ws)))
    obj = json.loads(capsys.readouterr().out)
    assert obj["result"] == "success"
    assert len(obj["benchmark"]) == 2
    # profiler payload made it through
    assert obj["benchmark"][0]["profiler"]["name"]
    assert obj["benchmark"][0]["cpu_count"] >= 1
