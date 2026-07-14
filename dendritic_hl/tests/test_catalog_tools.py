"""Integration tests for the non-build tools, driven through cmd_* on tmp_path.

No Halide required: these tools only touch the on-disk catalog.
"""

import json
import os

import pytest

from dendritic_hl_lib import tools
from dendritic_hl_lib.context import Context
from dendritic_hl_lib.errors import DhHlError
from conftest import ns


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_new_root_then_status_consistent(workspace, capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    out = capsys.readouterr().out
    assert "Created root schedule" in out

    tools.cmd_status(ns(workspace=str(workspace)))
    out = capsys.readouterr().out
    assert "workspace consistent" in out
    assert "no current idea" in out


def test_new_root_rejects_existing_major(workspace):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    with pytest.raises(DhHlError, match="already stored as a major schedule"):
        tools.cmd_new_root(ns(workspace=str(workspace)))


def test_idea_lifecycle(workspace, tmp_path, capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    capsys.readouterr()

    prop = _write(tmp_path, "prop.txt", "Vectorize wider.\nSecond line.\n")
    tools.cmd_new_idea(ns(workspace=str(workspace),
                          proposal_name="vec_wider", proposal=prop))
    idea_line = capsys.readouterr().out.strip()
    assert "Created idea" in idea_line

    # list_ideas prints the 3-line summary
    tools.cmd_list_ideas(ns(workspace=str(workspace)))
    listing = capsys.readouterr().out
    assert "vec_wider" in listing
    assert "Vectorize wider." in listing
    assert "Second line." not in listing  # only first line, truncated

    # view_idea + json_idea_info
    tools.cmd_view_idea(ns(workspace=str(workspace), idea=".vec_wider"))
    assert "vec_wider" in capsys.readouterr().out

    tools.cmd_json_idea_info(ns(workspace=str(workspace), idea=".vec_wider"))
    obj = json.loads(capsys.readouterr().out)
    assert obj["proposal_name"] == "vec_wider"
    assert obj["canonical_schedule"] is None
    assert obj["importance"] is None  # -inf -> null


def test_duplicate_proposal_name_rejected(workspace, tmp_path):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    prop = _write(tmp_path, "p.txt", "text")
    tools.cmd_new_idea(ns(workspace=str(workspace),
                          proposal_name="dup", proposal=prop))
    with pytest.raises(DhHlError, match="already used"):
        tools.cmd_new_idea(ns(workspace=str(workspace),
                              proposal_name="dup", proposal=prop))


def test_comment_shows_up_in_json(workspace, tmp_path, capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    capsys.readouterr()
    cfile = _write(tmp_path, "c.txt", "a remark")
    tools.cmd_comment_importance(ns(workspace=str(workspace),
                                    commentary=cfile, importance=5))
    capsys.readouterr()  # discard the "Added commentary" line
    tools.cmd_json_schedule_info(ns(workspace=str(workspace)))
    obj = json.loads(capsys.readouterr().out)
    assert len(obj["commentary"]) == 1
    assert obj["commentary"][0]["importance"] == 5
    assert obj["commentary"][0]["text"] == "a remark"


def test_restore_roundtrips_workspace(workspace, capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    sid = capsys.readouterr().out.split()[-1]
    original = workspace.read_text()

    workspace.write_text(original + "\n// scratch edit\n")
    assert workspace.read_text() != original

    tools.cmd_restore(ns(workspace=str(workspace), schedule=sid))
    assert workspace.read_text() == original

    # After restoring a root, status should be consistent again.
    tools.cmd_status(ns(workspace=str(workspace)))
    assert "workspace consistent" in capsys.readouterr().out


def test_status_no_catalog_advises_new_root(workspace, capsys):
    tools.cmd_status(ns(workspace=str(workspace)))
    out = capsys.readouterr().out
    assert "No catalog directory yet" in out
    assert "new_root" in out


def test_edited_workspace_is_inconsistent(workspace, capsys):
    tools.cmd_new_root(ns(workspace=str(workspace)))
    capsys.readouterr()
    workspace.write_text(workspace.read_text() + "\n// edit\n")
    tools.cmd_status(ns(workspace=str(workspace)))
    out = capsys.readouterr().out
    assert "inconsistent, unknown schedule" in out
    assert "AGENTS:" in out  # the warning banner
