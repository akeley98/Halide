"""CLI/tool coverage for golden objects (idea.md "Golden Object Tools"):
new_golden (the `none` schedule + the algorithm-hlpipe satisfiability gate),
golden_history, and json_golden_info.  Halide-free: the hlpipe build artifact
the gate looks for is fabricated directly in the session bin/."""

import json
import os

import pytest

from dendritic_hl_lib import build, locks, tools
from dendritic_hl_lib.errors import DhHlError
from conftest import open_catalog


def _reset():
    locks._reset_for_tests()


def _remarks(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _seed_canonical_id(session):
    """Full ID of the session seed idea's canonical schedule (a real,
    goldenable node)."""
    cat = open_catalog(session.catalog_dir)
    try:
        seed = cat.get_idea(cat.get_session(session.session_id).seed_idea_id)
        return seed.canonical
    finally:
        _reset()


def _fake_hlpipe(session, schedule_id, params_index=0):
    """Fabricate the algorithm-hlpipe build artifact new_golden gates on, at the
    exact path build.copy_build_output would read (session bin/ + the per-(node,
    params) subdir)."""
    rel = build._build_output_rel(schedule_id, "algorithm_hlpipe", params_index)
    path = os.path.join(session.private_dir, "bin", rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"fake hlpipe\n")
    return path


def _out(run_tool, capsys, fn, args):
    run_tool(fn, args)
    return capsys.readouterr().out


def test_new_golden_none_schedule(session, run_tool, capsys, tmp_path):
    rf = _remarks(tmp_path, "r.txt", "no schedule golden\n")
    gid = _out(run_tool, capsys, tools.cmd_new_golden,
               session.ns(remarks=rf, schedule="none")).strip()
    assert gid.startswith("golden_")
    cat = open_catalog(session.catalog_dir)
    try:
        assert cat.golden_schedule_node() is None          # ref is none
        assert cat.get_golden(gid).schedule_id is None
        assert cat.get_golden(gid).remarks == "no schedule golden\n"
    finally:
        _reset()


def test_new_golden_requires_built_hlpipe(session, run_tool, tmp_path):
    """A golden schedule with no algorithm hlpipe built in this session is
    refused (it could never satisfy a later golden check)."""
    sid = _seed_canonical_id(session)
    rf = _remarks(tmp_path, "r.txt", "x\n")
    with pytest.raises(DhHlError, match="no algorithm hlpipe built"):
        run_tool(tools.cmd_new_golden, session.ns(remarks=rf, schedule=sid))


def test_new_golden_with_built_schedule(session, run_tool, capsys, tmp_path):
    sid = _seed_canonical_id(session)
    _fake_hlpipe(session, sid)
    rf = _remarks(tmp_path, "r.txt", "the golden\n")
    gid = _out(run_tool, capsys, tools.cmd_new_golden,
               session.ns(remarks=rf, schedule=sid)).strip()
    cat = open_catalog(session.catalog_dir)
    try:
        assert cat.golden_schedule_node().full_id == sid
        assert cat.get_golden(gid).schedule_id == sid
    finally:
        _reset()


def test_golden_history_newest_first_and_json(session, run_tool, capsys, tmp_path):
    sid = _seed_canonical_id(session)
    _fake_hlpipe(session, sid)
    g1 = _out(run_tool, capsys, tools.cmd_new_golden,
              session.ns(remarks=_remarks(tmp_path, "a.txt", "first\n"),
                         schedule="none")).strip()
    g2 = _out(run_tool, capsys, tools.cmd_new_golden,
              session.ns(remarks=_remarks(tmp_path, "b.txt", "second\n"),
                         schedule=sid)).strip()

    hist = _out(run_tool, capsys, tools.cmd_golden_history, session.ns())
    # Most recent (g2) precedes the older (g1).
    assert hist.index("second") < hist.index("first")
    assert "schedule: none" in hist                        # g1's line
    # g2's schedule line shows the node's (short) ID, not 'none'.
    cat = open_catalog(session.catalog_dir)
    try:
        short = cat.format_schedule_id(cat.get_schedule(sid))
    finally:
        _reset()
    assert "schedule: " + short in hist

    # json_golden_info round-trips both, schedule null vs full ID.
    j2 = json.loads(_out(run_tool, capsys, tools.cmd_json_golden_info,
                         session.ns(golden=g2)))
    assert j2 == {"remarks": "second\n", "schedule": sid}
    j1 = json.loads(_out(run_tool, capsys, tools.cmd_json_golden_info,
                         session.ns(golden=g1)))
    assert j1 == {"remarks": "first\n", "schedule": None}


def test_json_export_includes_goldens(session, run_tool, capsys, tmp_path):
    sid = _seed_canonical_id(session)
    _fake_hlpipe(session, sid)
    gid = _out(run_tool, capsys, tools.cmd_new_golden,
               session.ns(remarks=_remarks(tmp_path, "r.txt", "exported\n"),
                          schedule=sid)).strip()
    obj = json.loads(_out(run_tool, capsys, tools.cmd_json_export, session.ns()))
    assert "goldens" in obj
    assert obj["goldens"][gid] == {"remarks": "exported\n", "schedule": sid}


def test_json_golden_info_unknown_id_errors(session, run_tool):
    with pytest.raises(DhHlError, match="no such golden"):
        run_tool(tools.cmd_json_golden_info,
                 session.ns(golden="golden_2020-01-01T000000_000000Z"))


# ---- A3: golden magic values in [schedule ID] -----------------------------
#
# The magic `[schedule ID]` values (golden, golden object IDs, terminus,
# session_output) are resolved by Context.resolve_schedule_arg -- NOT by the
# catalog-layer _resolve_schedule, which handles only plain full/short IDs.  So
# these drive resolution through a real tool (schedule_full_id, whose body is
# `ctx.resolve_schedule_arg(args.schedule)`), never _resolve_schedule directly.

def _make_golden(session, run_tool, capsys, tmp_path, name, schedule):
    _fake_hlpipe(session, schedule) if schedule != "none" else None
    return _out(run_tool, capsys, tools.cmd_new_golden,
                session.ns(remarks=_remarks(tmp_path, name, name + "\n"),
                           schedule=schedule)).strip()


def _resolved_full_id(run_tool, capsys, session, spec):
    """Resolve *spec* through the magic-aware resolver (Context.
    resolve_schedule_arg, via schedule_full_id) and return the printed full ID."""
    return _out(run_tool, capsys, tools.cmd_schedule_full_id,
                session.ns(schedule=spec)).strip()


def test_resolve_golden_magic_value(session, run_tool, capsys, tmp_path):
    sid = _seed_canonical_id(session)
    _make_golden(session, run_tool, capsys, tmp_path, "g", sid)
    # `golden` resolves to the golden schedule node.
    assert _resolved_full_id(run_tool, capsys, session, "golden") == sid


def test_resolve_golden_object_id(session, run_tool, capsys, tmp_path):
    sid = _seed_canonical_id(session)
    gid = _make_golden(session, run_tool, capsys, tmp_path, "g", sid)
    # A golden object ID resolves to *that* golden's schedule.
    assert _resolved_full_id(run_tool, capsys, session, gid) == sid


def test_resolve_golden_errors(session, run_tool, capsys, tmp_path):
    # No golden object at all.
    with pytest.raises(DhHlError, match="no golden schedule node"):
        run_tool(tools.cmd_schedule_full_id, session.ns(schedule="golden"))
    # A golden whose most-recent reference is none -> `golden` still errors, and
    # a golden ID with no schedule errors "references no schedule node".
    gid = _make_golden(session, run_tool, capsys, tmp_path, "none_g", "none")
    with pytest.raises(DhHlError, match="no golden schedule node"):
        run_tool(tools.cmd_schedule_full_id, session.ns(schedule="golden"))
    with pytest.raises(DhHlError, match="references no schedule node"):
        run_tool(tools.cmd_schedule_full_id, session.ns(schedule=gid))
    with pytest.raises(DhHlError, match="no such golden"):
        run_tool(tools.cmd_schedule_full_id,
                 session.ns(schedule="golden_2020-01-01T000000_000000Z"))


def test_other_golden_delegates_to_resolve_schedule_arg(session, run_tool,
                                                        capsys, tmp_path):
    """`init_build --other golden` works because build._resolve_other delegates a
    non-none/parent spec to ctx.resolve_schedule_arg (the sole magic-aware
    resolver); `none` stays the disabled sentinel, and the resolver is not
    consulted for it.  (The real end-to-end `--other golden` build is covered by
    test_golden_halide.py.)"""
    from dendritic_hl_lib import build as build_mod
    sid = _seed_canonical_id(session)
    cat = open_catalog(session.catalog_dir)
    try:
        target = cat.get_schedule(sid)
        golden_node = cat.get_schedule(sid)
        calls = []

        class _Ctx:
            def resolve_schedule_arg(self, spec):
                calls.append(spec)
                return golden_node

        # A non-none/parent spec is delegated verbatim to resolve_schedule_arg.
        assert build_mod._resolve_other(_Ctx(), "golden", target) is golden_node
        assert calls == ["golden"]
        # `none` is the disabled sentinel -- the resolver is never consulted.
        assert build_mod._resolve_other(_Ctx(), "none", target) is None
        assert calls == ["golden"]
    finally:
        _reset()
