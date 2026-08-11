"""Coverage for the profiler-warnings feature (idea.md "WarningToggle State",
`add_warning_toggle`, `debug_warning_toggle`, `view_benchmark_warnings`, and the
`json_schedule_info` warning_toggles output).

Three layers:
* pure-model tests over the WarningToggle sub-object + block algorithm;
* in-process CLI tests (run_tool) for the three tools;
* a real-subprocess (run_cli) end-to-end for view_benchmark_warnings.

The genuine-Halide profiler coverage lives in test_halide.py (needs a local
build).  Here the profiler warnings are fabricated so the harness logic is
tested without a compiler.
"""

import json

import pytest

from dendritic_hl_lib import build, ids, profiler_warnings, safety, tools
from dendritic_hl_lib.enums import Result
from dendritic_hl_lib.errors import DhHlError
from conftest import ns, open_catalog, make_catalog_session, Sess


# ---------------------------------------------------------------------------
# profiler_warnings: the isolation layer for the temporary delivery hack.
# ---------------------------------------------------------------------------

def test_warnings_from_temp_file(tmp_path):
    p = tmp_path / "w.json"
    # Absent file -> [] (profiler writes it only when there are warnings).
    assert profiler_warnings.warnings_from_temp_file(str(p)) == []
    # Empty file -> [].
    p.write_text("")
    assert profiler_warnings.warnings_from_temp_file(str(p)) == []
    # Single JSON object with a warnings list -> that list.
    p.write_text(json.dumps({"pipeline": "x", "warnings": [{"rule": "r"}]}))
    assert profiler_warnings.warnings_from_temp_file(str(p)) == [{"rule": "r"}]


def test_warning_accessors():
    w = {"rule": "no_vector_ops", "func": "hist_rows",
         "message": "m", "canonical_id": 3}
    assert profiler_warnings.warning_key(w) == ("no_vector_ops", "hist_rows")
    assert profiler_warnings.warning_message(w) == "m"
    # Missing keys degrade to None / "".
    assert profiler_warnings.warning_key({}) == (None, None)
    assert profiler_warnings.warning_message({}) == ""


def test_warnings_of_benchmark():
    assert profiler_warnings.warnings_of_benchmark({}) == []  # pre-warnings
    assert profiler_warnings.warnings_of_benchmark(
        {"warnings": [{"rule": "r"}]}) == [{"rule": "r"}]


# ---------------------------------------------------------------------------
# A tree exercising the "cancel not on the node-to-root path" subtlety.
# ---------------------------------------------------------------------------

def _tricky_catalog(tmp_path):
    """R{W1 blocks (no_vector_ops, hist_rows)} -> idea vec ->
        * Ca (canonical){Wc cancels W1}
        * Cb (minor){nothing}

    For Cb the path to root is [Cb, R]: W1 survives -> warning blocked.
    For Ca the path is [Ca, R]: Wc cancels W1 -> warning NOT blocked.
    Wc is deliberately off Cb's path, so it must NOT un-block Cb."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    I = cat.create_idea(R, "vec", "Vectorize hist_rows.\n")
    Ca = cat.create_schedule("ca", parent_idea=I)
    Cb = cat.create_schedule("cb", parent_idea=I)
    for c in (Ca, Cb):
        c.set_result(Result.SUCCESS)
    I.set_canonical(Ca.full_id)

    c_root = R.add_commentary("hist_rows is intentionally scalar; ignore.\n")
    W1 = R.add_warning_toggle(c_root.full_id, rule="no_vector_ops",
                              func="hist_rows")
    c_ca = Ca.add_commentary("Re-enable the vectorization warning here.\n")
    Wc = Ca.add_warning_toggle(c_ca.full_id, cancels=W1.full_id)

    cat.flush()
    safety.commit()
    return cat_dir, {"R": R.full_id, "I": I.full_id, "Ca": Ca.full_id,
                     "Cb": Cb.full_id, "W1": W1.full_id, "Wc": Wc.full_id,
                     "c_root": c_root.full_id, "c_ca": c_ca.full_id}


def test_block_algorithm_localized_to_subtree(tmp_path):
    cat_dir, ids_ = _tricky_catalog(tmp_path)
    cat = open_catalog(cat_dir)
    Cb = cat.get_schedule(ids_["Cb"])
    Ca = cat.get_schedule(ids_["Ca"])

    # Cb: W1 in effect, Wc off-path -> blocked.
    assert cat.blocking_toggle(Cb, "no_vector_ops", "hist_rows") is not None
    # Ca: Wc cancels W1 -> not blocked.
    assert cat.blocking_toggle(Ca, "no_vector_ops", "hist_rows") is None
    # A warning nobody blocks is never blocked.
    assert cat.blocking_toggle(Cb, "could_compute_further_inside", "x") is None


def test_warning_toggle_state_cancelled_flags(tmp_path):
    cat_dir, ids_ = _tricky_catalog(tmp_path)
    cat = open_catalog(cat_dir)
    Ca = cat.get_schedule(ids_["Ca"])
    toggles, cancelled = cat.warning_toggle_state(Ca)
    assert {w.full_id for w in toggles} == {ids_["W1"], ids_["Wc"]}
    assert cancelled == {ids_["W1"]}  # Wc is on-path here, so W1 is cancelled

    Cb = cat.get_schedule(ids_["Cb"])
    toggles_b, cancelled_b = cat.warning_toggle_state(Cb)
    assert {w.full_id for w in toggles_b} == {ids_["W1"]}
    assert cancelled_b == set()  # Wc is off Cb's path


# ---------------------------------------------------------------------------
# json_schedule_info warning_toggles output + ID round-trips.
# ---------------------------------------------------------------------------

def test_json_schedule_info_warning_toggles(tmp_path):
    cat_dir, ids_ = _tricky_catalog(tmp_path)
    cat = open_catalog(cat_dir)
    R = cat.get_schedule(ids_["R"])
    obj = tools._schedule_json(cat, R)
    assert len(obj["warning_toggles"]) == 1
    wt = obj["warning_toggles"][0]
    assert wt["id"] == ids_["W1"]
    assert wt["citation"] == ids_["c_root"]
    assert wt["rule"] == "no_vector_ops"
    assert wt["func"] == "hist_rows"
    assert wt["cancels"] is None

    Ca = cat.get_schedule(ids_["Ca"])
    wt_ca = tools._schedule_json(cat, Ca)["warning_toggles"][0]
    assert wt_ca["cancels"] == ids_["W1"]
    assert wt_ca["rule"] is None and wt_ca["func"] is None


def test_warning_toggle_id_round_trip(tmp_path):
    cat_dir, ids_ = _tricky_catalog(tmp_path)
    cat = open_catalog(cat_dir)
    R = cat.get_schedule(ids_["R"])
    w = R.warning_toggles[0]
    short = cat.format_warning_toggle_id(w)
    assert "." in short  # a real short ID, not the full ID
    assert cat.resolve_warning_toggle(short).full_id == w.full_id
    assert cat.resolve_warning_toggle(w.full_id).full_id == w.full_id


# ---------------------------------------------------------------------------
# In-process CLI: add_warning_toggle + debug_warning_toggle.
# ---------------------------------------------------------------------------

def _seed_with_commentary(tmp_path):
    """A session whose canonical (seed) schedule carries one commentary.  Returns
    (Sess, sched_full_id, commentary_full_id)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    catalog_dir, session_id = make_catalog_session(cat_dir)
    cat = open_catalog(cat_dir)
    seed_idea = cat.get_session(session_id).seed_idea_id
    sched_id = cat.get_idea(seed_idea).canonical
    node = cat.get_schedule(sched_id)
    c = node.add_commentary("Scalar hist row is intentional here.\n")
    cid = c.full_id
    cat.flush()
    safety.commit()
    return Sess(catalog_dir, session_id), sched_id, cid


def test_add_warning_toggle_block(tmp_path, run_tool, capsys):
    S, sched_id, cid = _seed_with_commentary(tmp_path)
    run_tool(tools.cmd_add_warning_toggle,
             S.ns(schedule=sched_id, commentary=cid,
                  block=["no_vector_ops", "hist_rows"], cancel=None))
    assert "Added WarningToggle" in capsys.readouterr().out

    run_tool(tools.cmd_json_schedule_info, S.ns(schedule=sched_id))
    obj = json.loads(capsys.readouterr().out)
    assert len(obj["warning_toggles"]) == 1
    assert obj["warning_toggles"][0]["rule"] == "no_vector_ops"
    assert obj["warning_toggles"][0]["citation"] == cid


def test_add_warning_toggle_requires_exactly_one_form(tmp_path, run_tool):
    S, sched_id, cid = _seed_with_commentary(tmp_path)
    # Neither --block nor --cancel.
    with pytest.raises(DhHlError):
        run_tool(tools.cmd_add_warning_toggle,
                 S.ns(schedule=sched_id, commentary=cid, block=None, cancel=None))
    # Both at once.
    with pytest.raises(DhHlError):
        run_tool(tools.cmd_add_warning_toggle,
                 S.ns(schedule=sched_id, commentary=cid,
                      block=["r", "f"], cancel="whatever"))


def test_debug_warning_toggle_lists_and_filters(tmp_path, run_tool, capsys):
    cat_dir, ids_ = _tricky_catalog(tmp_path)
    cat_dir_str = cat_dir

    # Debug Ca: both W1 and Wc are on-path; W1 shows cancelled: true.
    run_tool(tools.cmd_debug_warning_toggle,
             ns(catalog=cat_dir_str, schedule=ids_["Ca"]))
    out = capsys.readouterr().out
    assert "rule/func: no_vector_ops hist_rows" in out
    assert "cancels: " in out
    assert "cancelled: true" in out           # W1 cancelled on Ca's path
    # Cited commentary snippet came through.
    assert "hist_rows is intentionally scalar" in out

    # Debug Cb: only W1 on path, not cancelled.
    run_tool(tools.cmd_debug_warning_toggle,
             ns(catalog=cat_dir_str, schedule=ids_["Cb"]))
    out_b = capsys.readouterr().out
    assert "cancelled: false" in out_b
    assert "cancels: " not in out_b           # Wc is off Cb's path

    # --block filter keeps the blocking toggle (even if cancelled).
    run_tool(tools.cmd_debug_warning_toggle,
             ns(catalog=cat_dir_str, schedule=ids_["Ca"],
                block=["no_vector_ops", "hist_rows"], cancel=None))
    only_block = capsys.readouterr().out
    assert "id: " in only_block and "cancels: " not in only_block

    # --cancel filter keeps only the cancelling toggle.
    run_tool(tools.cmd_debug_warning_toggle,
             ns(catalog=cat_dir_str, schedule=ids_["Ca"],
                block=None, cancel=ids_["W1"]))
    only_cancel = capsys.readouterr().out
    assert "cancels: " in only_cancel and "rule/func:" not in only_cancel

    # --cancel on a nonexistent toggle is not an error; just no matches.
    run_tool(tools.cmd_debug_warning_toggle,
             ns(catalog=cat_dir_str, schedule=ids_["Ca"],
                block=None, cancel="deadbeef.2020-01-01T000000_000000Z"))
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# view_benchmark_warnings (fabricated benchmark, no Halide).
# ---------------------------------------------------------------------------

def _bench_warnings():
    return [
        {"rule": "no_vector_ops", "func": "hist_rows",
         "message": "hist_rows not vectorized", "canonical_id": 3},
        {"rule": "could_compute_further_inside", "func": "equalize",
         "message": "equalize could compute further inside", "canonical_id": 7},
    ]


def _catalog_with_benchmark(tmp_path, with_block=False):
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    R.set_result(Result.SUCCESS)
    data = {"hostname": "Testbox", "cpu_count": 4, "parameters": {},
            "profiler": {"name": "p"}, "warnings": _bench_warnings()}
    bench = R.add_benchmark("Testbox", data)
    bench_id = bench.full_id
    if with_block:
        c = R.add_commentary("hist_rows scalar is deliberate.\n")
        R.add_warning_toggle(c.full_id, rule="no_vector_ops", func="hist_rows")
    cat.flush()
    safety.commit()
    return cat_dir, bench_id


def test_view_benchmark_warnings_plain(tmp_path, run_tool, capsys):
    cat_dir, bench_id = _catalog_with_benchmark(tmp_path)
    run_tool(tools.cmd_view_benchmark_warnings,
             ns(catalog=cat_dir, benchmark=bench_id))
    out = capsys.readouterr().out
    assert "rule/func: no_vector_ops hist_rows" in out
    assert "message: hist_rows not vectorized" in out
    assert "rule/func: could_compute_further_inside equalize" in out
    assert "blocked by:" not in out


def test_view_benchmark_warnings_blocked(tmp_path, run_tool, capsys):
    cat_dir, bench_id = _catalog_with_benchmark(tmp_path, with_block=True)
    run_tool(tools.cmd_view_benchmark_warnings,
             ns(catalog=cat_dir, benchmark=bench_id))
    out = capsys.readouterr().out
    # The blocked warning hides its message and gains block/citation lines.
    assert "blocked by:" in out
    assert "citation:" in out
    assert "hist_rows scalar is deliberate" in out
    assert "message: hist_rows not vectorized" not in out
    # The other (unblocked) warning still shows its message.
    assert "message: equalize could compute further inside" in out


def test_view_benchmark_warnings_always_show_message(tmp_path, run_tool, capsys):
    cat_dir, bench_id = _catalog_with_benchmark(tmp_path, with_block=True)
    run_tool(tools.cmd_view_benchmark_warnings,
             ns(catalog=cat_dir, benchmark=bench_id, always_show_message=True))
    out = capsys.readouterr().out
    assert "message: hist_rows not vectorized" in out  # shown despite block
    assert "blocked by:" in out


# ---------------------------------------------------------------------------
# Real subprocess CLI (run_cli) for view_benchmark_warnings.
# ---------------------------------------------------------------------------

def test_view_benchmark_warnings_real_cli(tmp_path, run_cli):
    cat_dir, bench_id = _catalog_with_benchmark(tmp_path, with_block=True)
    r = run_cli("view_benchmark_warnings", "-C", cat_dir, bench_id)
    assert r.returncode == 0, r.stderr
    # Grep that the key: value lines showed up as expected.
    assert "rule/func: no_vector_ops hist_rows" in r.stdout
    assert "blocked by:" in r.stdout
    assert "citation:" in r.stdout
    assert "message: equalize could compute further inside" in r.stdout


# ---------------------------------------------------------------------------
# comment prints its ID; the citation workflow end-to-end.
# ---------------------------------------------------------------------------

def _comment_via_cli(run_tool, capsys, cat_dir, sched_id, tmp_path, text,
                     review="neutral"):
    """Run `comment` and return the commentary ID it prints (idea.md "Comment
    Tool" prints the new commentary's ID so it can be cited)."""
    cfile = tmp_path / "c.txt"
    cfile.write_text(text)
    run_tool(tools.cmd_comment,
             ns(catalog=cat_dir, schedule=sched_id, commentary=str(cfile),
                review=review, cancels=None))
    line = capsys.readouterr().out.strip()
    assert line.startswith("Added {} commentary ".format(review))
    return line.split("commentary ", 1)[1].split(" to ", 1)[0]


def test_citation_workflow_end_to_end(tmp_path, run_tool, capsys):
    cat_dir, bench_id = _catalog_with_benchmark(tmp_path)
    sched_id = ids.benchmark_schedule_id(bench_id)

    cid = _comment_via_cli(run_tool, capsys, cat_dir, sched_id, tmp_path,
                           "hist_rows is deliberately scalar.\n")
    # The printed ID resolves + views as a single commentary.
    run_tool(tools.cmd_view_commentary, ns(catalog=cat_dir, commentary=cid))
    v = capsys.readouterr().out
    assert "review: neutral" in v
    assert "hist_rows is deliberately scalar." in v

    # It can be cited by a WarningToggle, which then blocks the warning.
    run_tool(tools.cmd_add_warning_toggle,
             ns(catalog=cat_dir, schedule=sched_id, commentary=cid,
                block=["no_vector_ops", "hist_rows"], cancel=None))
    capsys.readouterr()
    run_tool(tools.cmd_view_benchmark_warnings,
             ns(catalog=cat_dir, benchmark=bench_id))
    out = capsys.readouterr().out
    assert "blocked by:" in out
    assert "hist_rows is deliberately scalar" in out  # citation snippet


def test_view_commentary_single_vs_all_and_brief(tmp_path, run_tool, capsys):
    cat_dir, bench_id = _catalog_with_benchmark(tmp_path)
    sched_id = ids.benchmark_schedule_id(bench_id)
    cid1 = _comment_via_cli(run_tool, capsys, cat_dir, sched_id, tmp_path,
                            "first remark line\nsecond line\n", review="positive")
    _comment_via_cli(run_tool, capsys, cat_dir, sched_id, tmp_path,
                     "another remark\n", review="negative")

    # view_commentary shows ONLY the referenced commentary.
    run_tool(tools.cmd_view_commentary, ns(catalog=cat_dir, commentary=cid1))
    one = capsys.readouterr().out
    assert "first remark line" in one
    assert "another remark" not in one

    # --brief prints just the first line, no full-text divider/second line.
    run_tool(tools.cmd_view_commentary,
             ns(catalog=cat_dir, commentary=cid1, brief=True))
    brief = capsys.readouterr().out
    assert "first remark line" in brief
    assert "second line" not in brief

    # view_all_commentary shows every commentary on the schedule.
    run_tool(tools.cmd_view_all_commentary, ns(catalog=cat_dir, schedule=sched_id))
    allout = capsys.readouterr().out
    assert "first remark line" in allout
    assert "another remark" in allout
