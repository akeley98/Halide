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

from dendritic_hl_lib import build, safety, tools
from conftest import make_catalog_session, open_catalog, ns, Sess, _PKG_ROOT

_BRIGHTEN = os.path.join(_PKG_ROOT, "rungen_example", "brighten_generator.cpp")
_HIST = os.path.join(_PKG_ROOT, "tests", "hist_opus_no_peeking.cpp")

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


# ---------------------------------------------------------------------------
# Profiler warnings, end-to-end through the real profiler.
#
# hist_opus_no_peeking.cpp reliably triggers, among others, the two warnings
# (no_vector_ops, hist_rows) and (could_compute_further_inside, equalize).
# These assertions are allowed to rot if the profiler's rule set changes.
# ---------------------------------------------------------------------------

@pytest.fixture
def hist_session(tmp_path, reset_safety):
    source = open(_HIST, encoding="utf-8").read()
    cat_dir = str(tmp_path / "hist.dh_hl")
    catalog_dir, session_id = make_catalog_session(cat_dir, source=source)
    return Sess(catalog_dir, session_id)


@pytest.mark.skipif(not os.path.isfile(_HIST), reason="hist generator missing")
def test_view_benchmark_warnings_real_halide(hist_session, run_tool, capsys):
    S = hist_session
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_profile, S.ns())
    assert e.value.code == 0
    # profile prints "Benchmark ID: <id>" for each saved benchmark.
    bench_ids = [ln.split("Benchmark ID: ", 1)[1]
                 for ln in capsys.readouterr().out.splitlines()
                 if ln.startswith("Benchmark ID: ")]
    assert len(bench_ids) == 1
    bench_id = bench_ids[0]

    # Unblocked view: both cited warnings show, with their messages.
    run_tool(tools.cmd_view_benchmark_warnings,
             ns(catalog=S.catalog_dir, benchmark=bench_id))
    out = capsys.readouterr().out
    assert "rule/func: no_vector_ops hist_rows" in out
    assert "rule/func: could_compute_further_inside equalize" in out
    assert "message:" in out
    assert "blocked by:" not in out

    # Block (no_vector_ops, hist_rows) with a WarningToggle on the schedule.
    cat = open_catalog(S.catalog_dir)
    seed = cat.get_session(S.session_id).seed_idea_id
    sched_id = cat.get_idea(seed).canonical
    node = cat.get_schedule(sched_id)
    c = node.add_commentary("hist_rows is intentionally scalar here.\n")
    node.add_warning_toggle(c.full_id, rule="no_vector_ops", func="hist_rows")
    cat.flush()
    safety.commit()

    # Re-view: the blocked warning hides its message + gains block/citation
    # lines; the other cited warning is untouched.
    run_tool(tools.cmd_view_benchmark_warnings,
             ns(catalog=S.catalog_dir, benchmark=bench_id))
    blocked = capsys.readouterr().out
    assert "blocked by:" in blocked
    assert "citation:" in blocked
    assert "hist_rows is intentionally scalar" in blocked
    assert "rule/func: could_compute_further_inside equalize" in blocked

    # debug_warning_toggle lists the toggle for this schedule, not cancelled.
    run_tool(tools.cmd_debug_warning_toggle,
             ns(catalog=S.catalog_dir, schedule=sched_id))
    dbg = capsys.readouterr().out
    assert "rule/func: no_vector_ops hist_rows" in dbg
    assert "cancelled: false" in dbg
