"""Problem objects: model (create/dup/validate), state transitions, short-ID
resolution/formatting, the CRUD/query tools, new_catalog's default problem, and
json_export inclusion (idea.md "Problem Object State" / "Problem Object Tools")."""

import json
import os

import pytest

from dendritic_hl_lib import catalog as catalog_mod
from dendritic_hl_lib import safety, tools
from dendritic_hl_lib.enums import ProblemState
from dendritic_hl_lib.errors import DhHlError

from conftest import ns, open_catalog


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

def _fresh_catalog(tmp_path):
    cat = open_catalog(str(tmp_path / "proj.dh_hl"))
    cat.ensure_created()
    return cat


def test_create_reload_roundtrip(tmp_path, reset_safety):
    cat = _fresh_catalog(tmp_path)
    p = cat.create_problem(["<RunGenMain>", "--benchmarks=all"], "default",
                           state=ProblemState.MAIN)
    full_id = p.full_id
    cat.flush()
    safety.commit()

    cat2 = open_catalog(str(tmp_path / "proj.dh_hl"))
    q = cat2.get_problem(full_id)
    assert q.argv == ["<RunGenMain>", "--benchmarks=all"]
    assert q.state == ProblemState.MAIN
    assert q.short_name == "default"
    # Full ID is the content hash of the canonical argv text.
    assert full_id == catalog_mod.ids.sha256_hex(
        catalog_mod.dump_problem_argv(["<RunGenMain>", "--benchmarks=all"]))


def test_duplicate_argv_errors_with_existing_id(tmp_path, reset_safety):
    cat = _fresh_catalog(tmp_path)
    cat.create_problem(["./runner", "<Lib>"], "a")
    with pytest.raises(DhHlError) as e:
        cat.create_problem(["./runner", "<Lib>"], "b")
    # Names the existing problem (short ID, since it's enabled).
    assert "already exists" in str(e.value)
    assert "problem.a" in str(e.value)


@pytest.mark.parametrize("argv, msg", [
    ([], "at least one"),
    (["a", "<RunGenMain>"], "only valid as the first"),
    (["<RunGenMain>", "<Lib>"], "Cannot give both"),
    (["<Bogus>"], "unknown special argument"),
    (["./r", "<lib>"], "unknown special argument"),  # case-sensitive
])
def test_argv_validation_rejects(argv, msg):
    with pytest.raises(DhHlError) as e:
        catalog_mod.validate_problem_argv(argv)
    assert msg in str(e.value)


def test_argv_validation_accepts_custom_runner_with_lib():
    # A custom runner (argv[0] not <RunGenMain>) may carry <Lib> anywhere.
    catalog_mod.validate_problem_argv(["./runner", "--n=4", "<Lib>", "-v"])


def test_short_name_validation(tmp_path, reset_safety):
    cat = _fresh_catalog(tmp_path)
    with pytest.raises(DhHlError):
        cat.create_problem(["<RunGenMain>"], "bad name")  # space
    with pytest.raises(DhHlError):
        cat.create_problem(["<RunGenMain>"], "")


def test_main_uniqueness_and_transitions(tmp_path, reset_safety):
    cat = _fresh_catalog(tmp_path)
    a = cat.create_problem(["<RunGenMain>", "1"], "a", state=ProblemState.MAIN)
    b = cat.create_problem(["<RunGenMain>", "2"], "b")
    cat.flush()
    safety.commit()
    assert cat.main_problem().full_id == a.full_id

    # Promote b -> main demotes a -> enabled.
    b.set_state(ProblemState.MAIN)
    a.set_state(ProblemState.ENABLED)
    assert cat.main_problem().full_id == b.full_id
    assert a.state == ProblemState.ENABLED


def test_main_problem_errors_when_absent_or_multiple(tmp_path, reset_safety):
    cat = _fresh_catalog(tmp_path)
    cat.create_problem(["<RunGenMain>", "1"], "a")  # enabled, no main
    with pytest.raises(DhHlError) as e:
        cat.main_problem()
    assert "no main problem" in str(e.value)


def test_enabled_problems_excludes_disabled(tmp_path, reset_safety):
    cat = _fresh_catalog(tmp_path)
    m = cat.create_problem(["<RunGenMain>", "1"], "m", state=ProblemState.MAIN)
    e = cat.create_problem(["<RunGenMain>", "2"], "e")
    d = cat.create_problem(["<RunGenMain>", "3"], "d", state=ProblemState.DISABLED)
    ids_enabled = {p.full_id for p in cat.enabled_problems()}
    assert ids_enabled == {m.full_id, e.full_id}
    assert d.full_id not in ids_enabled


# ---------------------------------------------------------------------------
# resolve / format short IDs
# ---------------------------------------------------------------------------

def test_resolve_main_full_and_short(tmp_path, reset_safety):
    cat = _fresh_catalog(tmp_path)
    p = cat.create_problem(["<RunGenMain>", "1"], "solo", state=ProblemState.MAIN)
    cat.flush()
    safety.commit()
    assert cat.resolve_problem("main").full_id == p.full_id
    assert cat.resolve_problem(p.full_id).full_id == p.full_id
    assert cat.resolve_problem("problem.solo").full_id == p.full_id
    assert cat.format_problem_id(p) == "problem.solo"


def test_disabled_problem_has_no_short_id(tmp_path, reset_safety):
    cat = _fresh_catalog(tmp_path)
    p = cat.create_problem(["<RunGenMain>", "1"], "hidden", state=ProblemState.DISABLED)
    # A disabled problem is not matched by problem.{name}, so it formats as full.
    assert cat.format_problem_id(p) == p.full_id
    with pytest.raises(DhHlError):
        cat.resolve_problem("problem.hidden")


def test_ambiguous_short_name_falls_back_to_full(tmp_path, reset_safety):
    cat = _fresh_catalog(tmp_path)
    a = cat.create_problem(["<RunGenMain>", "1"], "dup")
    b = cat.create_problem(["<RunGenMain>", "2"], "dup")
    # Two enabled problems share the short name: resolution is ambiguous, and
    # formatting falls back to the full ID for both.
    with pytest.raises(DhHlError) as e:
        cat.resolve_problem("problem.dup")
    assert "ambiguous" in str(e.value)
    assert cat.format_problem_id(a) == a.full_id
    assert cat.format_problem_id(b) == b.full_id


@pytest.mark.parametrize("corrupt", ["garbage", None])
def test_malformed_or_missing_state_defaults_enabled_with_warning(
        tmp_path, reset_safety, capsys, corrupt):
    cat = _fresh_catalog(tmp_path)
    p = cat.create_problem(["<RunGenMain>", "1"], "p")
    cat.flush()
    safety.commit()
    # Corrupt (garbage) or remove state.txt out-of-band; both warn + default.
    state_path = os.path.join(p.dir, "state.txt")
    if corrupt is None:
        os.remove(state_path)
    else:
        with open(state_path, "w", encoding="utf-8") as f:
            f.write(corrupt + "\n")
    cat2 = open_catalog(str(tmp_path / "proj.dh_hl"))
    assert cat2.get_problem(p.full_id).state == ProblemState.ENABLED
    assert "malformed problem state" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def _catalog_dir(tmp_path):
    """A bare (problem-only) catalog directory for the -C tools."""
    cat = _fresh_catalog(tmp_path)
    cat.flush()
    safety.commit()
    return cat.catalog_dir


def _out(run_tool, capsys, fn, args):
    run_tool(fn, args)
    return capsys.readouterr().out


def test_new_problem_and_listing_tools(tmp_path, run_tool, capsys, reset_safety):
    cat_dir = _catalog_dir(tmp_path)
    run_tool(tools.cmd_new_problem,
             ns(catalog=cat_dir, short_name="m",
                argv=["<RunGenMain>", "--benchmarks=all"]))
    run_tool(tools.cmd_set_main_problem, ns(catalog=cat_dir, problem="problem.m"))
    run_tool(tools.cmd_new_problem,
             ns(catalog=cat_dir, short_name="lib", argv=["./runner", "<Lib>"]))
    capsys.readouterr()

    all_out = _out(run_tool, capsys, tools.cmd_list_all_problems, ns(catalog=cat_dir))
    assert all_out.count("id: ") == 2
    assert "cli: [\"./runner\", \"<Lib>\"]" in all_out

    # json_problem_info round-trips the fields.
    j = json.loads(_out(run_tool, capsys, tools.cmd_json_problem_info,
                        ns(catalog=cat_dir, problem="main")))
    assert j == {"argv": ["<RunGenMain>", "--benchmarks=all"], "state": "main",
                 "short_name": "m"}


def test_enable_disable_setmain_tools(tmp_path, run_tool, capsys, reset_safety):
    cat_dir = _catalog_dir(tmp_path)
    run_tool(tools.cmd_new_problem,
             ns(catalog=cat_dir, short_name="a", argv=["<RunGenMain>", "1"]))
    run_tool(tools.cmd_new_problem,
             ns(catalog=cat_dir, short_name="b", argv=["<RunGenMain>", "2"]))
    run_tool(tools.cmd_set_main_problem, ns(catalog=cat_dir, problem="problem.a"))
    capsys.readouterr()

    # enable on the main leaves it main.
    run_tool(tools.cmd_enable_problem, ns(catalog=cat_dir, problem="problem.a"))
    j = json.loads(_out(run_tool, capsys, tools.cmd_json_problem_info,
                        ns(catalog=cat_dir, problem="main")))
    assert j["short_name"] == "a"

    # set_main to b demotes a.
    run_tool(tools.cmd_set_main_problem, ns(catalog=cat_dir, problem="problem.b"))
    ja = json.loads(_out(run_tool, capsys, tools.cmd_json_problem_info,
                         ns(catalog=cat_dir, problem="problem.a")))
    assert ja["state"] == "enabled"

    # disable a: no longer enabled -> absent from list_enabled_problems.
    run_tool(tools.cmd_disable_problem, ns(catalog=cat_dir, problem="problem.a"))
    enabled = _out(run_tool, capsys, tools.cmd_list_enabled_problems,
                   ns(catalog=cat_dir))
    assert "short name: a" not in enabled
    assert "short name: b" in enabled


def test_set_problem_short_name(tmp_path, run_tool, capsys, reset_safety):
    cat_dir = _catalog_dir(tmp_path)
    run_tool(tools.cmd_new_problem,
             ns(catalog=cat_dir, short_name="old", argv=["<RunGenMain>", "1"]))
    run_tool(tools.cmd_set_problem_short_name,
             ns(catalog=cat_dir, problem="problem.old", short_name="new"))
    capsys.readouterr()
    out = _out(run_tool, capsys, tools.cmd_get_problem_short_name,
               ns(catalog=cat_dir, problem="problem.new"))
    assert out.strip() == "new"


# ---------------------------------------------------------------------------
# new_catalog default problem + json_export
# ---------------------------------------------------------------------------

def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_new_catalog_creates_default_main_problem(tmp_path, run_tool, capsys,
                                                  reset_safety):
    cat_dir = str(tmp_path / "fresh.dh_hl")
    run_tool(tools.cmd_new_catalog, ns(
        catalog=cat_dir, proposal_name="seed",
        proposal=_write(tmp_path, "p.txt", "prompt\n"),
        input_cpp=_write(tmp_path, "g.cpp", "// gen\n"),
        input_parameters=None))
    capsys.readouterr()

    j = json.loads(_out(run_tool, capsys, tools.cmd_json_problem_info,
                        ns(catalog=cat_dir, problem="main")))
    assert j == {"argv": ["<RunGenMain>", "--benchmarks=all", "--estimate_all"],
                 "state": "main", "short_name": "default"}


def test_json_export_includes_problems(tmp_path, run_tool, capsys, reset_safety):
    cat_dir = _catalog_dir(tmp_path)
    run_tool(tools.cmd_new_problem,
             ns(catalog=cat_dir, short_name="x", argv=["<RunGenMain>", "1"]))
    capsys.readouterr()
    obj = json.loads(_out(run_tool, capsys, tools.cmd_json_export,
                          ns(catalog=cat_dir)))
    assert "problems" in obj
    (only,) = obj["problems"].values()
    assert only == {"argv": ["<RunGenMain>", "1"], "state": "enabled",
                    "short_name": "x"}
