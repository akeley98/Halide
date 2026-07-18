"""Phase 4: session lifecycle + query tools, driven in-process via run_tool.

The `session` fixture supplies a depth-0 top-level session whose workspace is
consistent with its seed idea's canonical schedule (see conftest)."""

import json
import os

import pytest

from dendritic_hl_lib import tools
from dendritic_hl_lib.errors import DhHlError
from conftest import ns


def _out(run_tool, capsys, fn, args):
    capsys.readouterr()
    run_tool(fn, args)
    return capsys.readouterr().out


def _line_after(out, prefix):
    for line in out.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise AssertionError("no line starting {!r} in:\n{}".format(prefix, out))


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


# ---- new_catalog ----------------------------------------------------------

def test_new_catalog_creates_everything(tmp_path, run_tool, capsys):
    cat_dir = str(tmp_path / "fresh.dh_hl")
    prop = _write(tmp_path, "p.txt", "explore tiling\n")
    inp = _write(tmp_path, "in.cpp", "// generator source\n")
    out = _out(run_tool, capsys, tools.cmd_new_catalog,
               ns(catalog=cat_dir, proposal_name="seed", proposal=prop, input_cpp=inp))
    assert "Created catalog" in out
    sid = _line_after(out, "Session: ")
    assert sid.startswith("0_")  # depth-0 top-level

    # Skeleton on disk: two schedules (root + canonical dup), one idea, one session.
    assert len(os.listdir(os.path.join(cat_dir, "sch"))) == 2
    assert len(os.listdir(os.path.join(cat_dir, "idea"))) == 1
    assert os.listdir(os.path.join(cat_dir, "session")) == [sid]
    # Private workspace holds the input C++, pointing at the seed idea.
    ws = os.path.join(cat_dir, "private", sid, "generator.cpp")
    assert open(ws).read() == "// generator source\n"

    # And it's immediately a consistent, open terminus.
    st = _out(run_tool, capsys, tools.cmd_status, ns(catalog=cat_dir, session=sid))
    assert "workspace consistent" in st


def test_new_catalog_rejects_existing_dir(session, run_tool, tmp_path):
    prop = _write(tmp_path, "p.txt", "x\n")
    inp = _write(tmp_path, "in.cpp", "y\n")
    with pytest.raises(DhHlError, match="already exists"):
        run_tool(tools.cmd_new_catalog,
                 ns(catalog=session.catalog_dir, proposal_name="seed",
                    proposal=prop, input_cpp=inp))


# ---- sub-sessions ---------------------------------------------------------

def test_new_sub_session(session, run_tool, capsys, tmp_path):
    prop = _write(tmp_path, "p.txt", "sub-agent task\n")
    out = _out(run_tool, capsys, tools.cmd_new_sub_session,
               session.ns(proposal_name="subtask", proposal=prop))
    sub_id = _line_after(out, "Created sub-session ")
    assert sub_id.startswith("1_")  # depth+1

    info = json.loads(_out(run_tool, capsys, tools.cmd_json_session_info,
                           ns(catalog=session.catalog_dir, session=sub_id)))
    assert info["depth"] == 1
    assert info["parent"] == session.session_id
    assert info["output_schedule"] is None
    # The proposal text got the "Created for session" line appended.
    iout = _out(run_tool, capsys, tools.cmd_view_session_idea,
                ns(catalog=session.catalog_dir, session=sub_id))
    assert "sub-agent task" in iout
    assert "Created for session: " + sub_id in iout

    # The parent now lists the sub as a child.
    pinfo = json.loads(_out(run_tool, capsys, tools.cmd_json_session_info,
                            session.ns()))
    assert sub_id in pinfo["children"]


# ---- close / successor / delist ------------------------------------------

def _comment_importance(session, run_tool, tmp_path, importance=5):
    cfile = _write(tmp_path, "c.txt", "session summary\n")
    run_tool(tools.cmd_comment_importance,
             session.ns(commentary=cfile, importance=importance))


def test_close_session_requires_positive_commentary(session, run_tool):
    with pytest.raises(DhHlError, match="positive importance"):
        run_tool(tools.cmd_close_session, session.ns())


def test_close_then_successor(session, run_tool, capsys, tmp_path):
    _comment_importance(session, run_tool, tmp_path)
    out = _out(run_tool, capsys, tools.cmd_close_session, session.ns())
    assert "Closed session" in out

    info = json.loads(_out(run_tool, capsys, tools.cmd_json_session_info,
                           session.ns()))
    assert info["output_schedule"] is not None

    # Closing again is refused.
    with pytest.raises(DhHlError, match="already has an output schedule"):
        run_tool(tools.cmd_close_session, session.ns())

    # A closed depth-0 session is still the terminus (closed terminus is normal),
    # but no longer "open".
    termini = _out(run_tool, capsys, tools.cmd_list_termini,
                   ns(catalog=session.catalog_dir))
    assert session.session_id in termini
    opens = _out(run_tool, capsys, tools.cmd_list_open_sessions,
                 ns(catalog=session.catalog_dir))
    assert session.session_id not in opens

    # Now a successor can start.
    prop = _write(tmp_path, "succ.txt", "next round\n")
    sout = _out(run_tool, capsys, tools.cmd_new_successor_session,
                session.ns(proposal_name="round2", proposal=prop))
    succ_id = _line_after(sout, "Created successor session ")
    assert succ_id.startswith("0_")  # successor is also top-level

    # The original is no longer a terminus; the successor is.
    termini = _out(run_tool, capsys, tools.cmd_list_termini,
                   ns(catalog=session.catalog_dir))
    assert session.session_id not in termini
    assert succ_id in termini


def test_successor_requires_self_closed(session, run_tool, tmp_path):
    prop = _write(tmp_path, "s.txt", "x\n")
    with pytest.raises(DhHlError, match="self-closed"):
        run_tool(tools.cmd_new_successor_session,
                 session.ns(proposal_name="r2", proposal=prop))


def test_delist_session(session, run_tool, capsys):
    run_tool(tools.cmd_delist_session, session.ns())
    info = json.loads(_out(run_tool, capsys, tools.cmd_json_session_info,
                           session.ns()))
    assert info["delisted"] is True
    # Delisted -> not a terminus, and closed (not open).
    termini = _out(run_tool, capsys, tools.cmd_list_termini,
                   ns(catalog=session.catalog_dir))
    assert session.session_id not in termini


# ---- copy / id-of / workspace / views ------------------------------------

def test_copy_and_id_getters(session, run_tool, capsys, tmp_path):
    # seed-schedule getters (the seed idea's canonical, == the consistent dup).
    seed_full = _out(run_tool, capsys, tools.cmd_seed_schedule_full_id,
                     session.ns()).strip()
    assert len(seed_full) == 90  # a schedule full ID

    dest = str(tmp_path / "copied.cpp")
    run_tool(tools.cmd_copy_session_seed_schedule,
             session.ns(output=dest))
    from conftest import DUMMY_SOURCE
    assert open(dest).read() == DUMMY_SOURCE

    # workspace path getters point into private/{id}.
    wpath = _out(run_tool, capsys, tools.cmd_workspace_schedule,
                 session.ns()).strip()
    assert wpath.endswith(os.path.join("private", session.session_id, "generator.cpp"))
    bpath = _out(run_tool, capsys, tools.cmd_workspace_bin, session.ns()).strip()
    assert bpath.endswith(os.path.join("private", session.session_id, "bin"))

    # session identity getters.
    assert _out(run_tool, capsys, tools.cmd_session_full_id,
                session.ns()).strip() == session.session_id
    handle = _out(run_tool, capsys, tools.cmd_session_handle,
                  session.ns()).strip()
    assert handle.startswith("tmp.")


def test_terminus_and_output_getters_after_close(session, run_tool, capsys, tmp_path):
    _comment_importance(session, run_tool, tmp_path)
    run_tool(tools.cmd_close_session, session.ns())

    out_full = _out(run_tool, capsys, tools.cmd_session_output_full_id,
                    session.ns()).strip()
    term_full = _out(run_tool, capsys, tools.cmd_terminus_schedule_full_id,
                     ns(catalog=session.catalog_dir)).strip()
    # The unique terminus's output is this session's output.
    assert out_full == term_full


def test_view_commentary(session, run_tool, capsys, tmp_path):
    _comment_importance(session, run_tool, tmp_path, importance=7)
    out = _out(run_tool, capsys, tools.cmd_view_commentary, session.ns())
    assert "importance: 7" in out
    assert "session summary" in out


def test_json_export_has_all_categories(session, run_tool, capsys):
    obj = json.loads(_out(run_tool, capsys, tools.cmd_json_export,
                          ns(catalog=session.catalog_dir)))
    assert set(obj) == {"ideas", "schedules", "sessions"}
    assert session.session_id in obj["sessions"]
    assert len(obj["schedules"]) == 2  # root + canonical dup
    assert len(obj["ideas"]) == 1
