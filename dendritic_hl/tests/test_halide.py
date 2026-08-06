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
from conftest import (make_catalog_session, open_catalog, ns, Sess, _PKG_ROOT,
                      branch_fresh_idea)

_BRIGHTEN = os.path.join(_PKG_ROOT, "rungen_example", "brighten_generator.cpp")
_HIST = os.path.join(_PKG_ROOT, "tests", "hist_opus_before_peeking.cpp")

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


def _stmt_line(path):
    return path


def test_build_and_profile_real_halide(brighten_session, run_tool, capsys):
    S = brighten_session
    # Give the target two parameters objects, then init_build --target workspace
    # (workspace now inconsistent -> a new child node with those params).  The
    # seed idea already has a canonical, so branch a fresh idea first (idea.md
    # "Init-Build Tool": init_build won't add children to a canonicalized idea).
    branch_fresh_idea(S)
    S.write_params('[{"offset": 5}, {"offset": 30}]')
    run_tool(build.cmd_init_build,
             S.ns(target="workspace", other="none", anchor="none"))
    capsys.readouterr()

    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, S.ns(profile=1, only="all"))
    assert e.value.code == 0
    # build announces both emitted stmt paths for the target; both exist on disk.
    printed = capsys.readouterr().out.splitlines()
    stmt_lines = [ln.split("dh_hl: stmt: ", 1)[1]
                  for ln in printed if ln.startswith("dh_hl: stmt: ")]
    plain = [p for p in stmt_lines if not p.endswith(".conceptual.stmt")]
    conceptual = [p for p in stmt_lines if p.endswith(".conceptual.stmt")]
    assert len(plain) == 2 and all(os.path.isfile(p) for p in plain)
    assert len(conceptual) == 2 and all(os.path.isfile(p) for p in conceptual)

    run_tool(tools.cmd_json_schedule_info, S.ns(schedule=None))
    obj = json.loads(capsys.readouterr().out)
    assert obj["result"] == "success"
    assert len(obj["benchmark"]) == 2
    assert sorted(b["parameters"]["offset"] for b in obj["benchmark"]) == [5, 30]
    # profiler payload made it through
    assert obj["benchmark"][0]["profiler"]["name"]
    assert obj["benchmark"][0]["cpu_count"] >= 1


# ---------------------------------------------------------------------------
# Profiler warnings, end-to-end through the real profiler.
#
# hist_opus_before_peeking.cpp reliably triggers, among others, the two warnings
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
    run_tool(build.cmd_init_build,
             S.ns(target="workspace", other="none", anchor="none"))
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, S.ns(profile=1, only="all"))
    assert e.value.code == 0
    # build prints "dh_hl: ... with Benchmark ID: <id>" for each saved benchmark.
    bench_ids = [ln.split("Benchmark ID: ", 1)[1]
                 for ln in capsys.readouterr().out.splitlines()
                 if "Benchmark ID: " in ln]
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


# ---------------------------------------------------------------------------
# Build command trace: command sequence, ordering relative to locks, and that
# the profiling order is randomized across batches.  Runs the REAL toolchain
# in-process (via run_tool, like the tests above), observing the shared trace
# sink -- no monkeypatching of the toolchain.  `build.py` records its commands
# onto locks._trace_sink (see build.py "observability" note).
# ---------------------------------------------------------------------------

def test_build_command_trace_and_shuffle(brighten_session, run_tool, capsys):
    from dendritic_hl_lib import locks
    S = brighten_session
    # A target with THREE parameters objects (+ the root as `other`) gives four
    # binaries, so a per-batch profiling order has 4! = 24 possibilities.
    S.write_params('[{"offset": 1}, {"offset": 2}, {"offset": 3}]')
    run_tool(build.cmd_init_build,
             S.ns(target="workspace", other="parent", anchor="none"))
    capsys.readouterr()

    BATCHES = 10
    with pytest.raises(SystemExit) as e:
        run_tool(build.cmd_build, S.ns(profile=BATCHES, only="all"))
    assert e.value.code == 0
    sink = list(locks._trace_sink)  # captured AFTER the build call (run_tool reset)

    def idx(event):
        return sink.index(event)

    # -- ordering relative to locks ------------------------------------------
    mach_excl = idx(("machine", "exclusive"))
    cat_excl = idx(("catalog", "exclusive"))
    assert idx(("machine", "shared")) < idx(("session", "exclusive")) < mach_excl
    assert mach_excl < cat_excl
    compile_ix = [i for i, ev in enumerate(sink)
                  if ev[0] == "build" and ev[1] in ("cpp_compile", "emit", "link")]
    profile_ix = [i for i, ev in enumerate(sink)
                  if ev[0] == "build" and ev[1] == "profile"]
    # All compilation happens before the machine-exclusive upgrade + catalog lock;
    # all profiling happens after the catalog lock.
    assert compile_ix and profile_ix
    assert max(compile_ix) < mach_excl < cat_excl < min(profile_ix)

    # -- command sequence as expected ----------------------------------------
    build_evs = [ev for ev in sink if ev[0] == "build"]
    node_ids = {ev[2] for ev in build_evs if ev[1] == "cpp_compile"}
    assert len(node_ids) == 2                       # target + other (root)
    emits = [ev for ev in build_evs if ev[1] == "emit"]
    links = [ev for ev in build_evs if ev[1] == "link"]
    assert len(emits) == 4 and len(links) == 4      # (3 target params) + (1 root)
    # Every batch profiles all four binaries.
    profiles = [ev for ev in build_evs if ev[1] == "profile"]
    assert len(profiles) == 4 * BATCHES

    # -- profiling order is randomized (not always identical) ----------------
    batch_starts = [i for i, ev in enumerate(sink)
                    if ev[0] == "build" and ev[1] == "batch"]
    assert len(batch_starts) == BATCHES
    orders = []
    for b, start in enumerate(batch_starts):
        end = batch_starts[b + 1] if b + 1 < len(batch_starts) else len(sink)
        order = tuple((ev[2], ev[3]) for ev in sink[start:end]
                      if ev[0] == "build" and ev[1] == "profile")
        assert len(order) == 4          # each batch profiles all four binaries
        orders.append(order)
    # With 4! orderings over 10 batches, all-identical is astronomically unlikely
    # unless the shuffle is broken.
    assert len(set(orders)) > 1, "profiling order never varied across batches"
