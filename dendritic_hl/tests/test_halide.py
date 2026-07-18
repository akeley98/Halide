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
from conftest import make_catalog_session, Sess, _PKG_ROOT

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
def brighten_session(tmp_path, reset_safety):
    source = open(_BRIGHTEN, encoding="utf-8").read()
    cat_dir = str(tmp_path / "proj.dh_hl")
    catalog_dir, session_id = make_catalog_session(cat_dir, source=source)
    return Sess(catalog_dir, session_id)


def test_build_and_profile_real_halide(brighten_session, run_tool, tmp_path,
                                       capsys):
    S = brighten_session
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, S.ns())
    assert e.value.code == 0
    # build prints both emitted stmt paths; both should really exist on disk.
    printed = capsys.readouterr().out.splitlines()
    stmt_lines = [ln for ln in printed if ln.endswith(".stmt")]
    plain = [ln for ln in stmt_lines if not ln.endswith(".conceptual.stmt")]
    conceptual = [ln for ln in stmt_lines if ln.endswith(".conceptual.stmt")]
    assert len(plain) == 1 and os.path.isfile(plain[0])
    assert len(conceptual) == 1 and os.path.isfile(conceptual[0])

    params = tmp_path / "p.json"
    params.write_text('[{"offset": 5}, {"offset": 30}]')
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_profile, S.ns(parameters=str(params)))
    assert e.value.code == 0

    capsys.readouterr()  # discard profile output before reading the JSON
    run_tool(tools.cmd_json_schedule_info, S.ns())
    obj = json.loads(capsys.readouterr().out)
    assert obj["result"] == "success"
    assert len(obj["benchmark"]) == 2
    # profiler payload made it through
    assert obj["benchmark"][0]["profiler"]["name"]
    assert obj["benchmark"][0]["cpu_count"] >= 1
