"""`dh_hl help <command>`: detailed per-command help sourced from idea.md, and
the doc<->code single-source drift guard."""

import types

from dendritic_hl_lib import main


def test_every_command_is_documented_in_idea_md():
    """The single-source guard: the set of CLI commands and the set of commands
    idea.md documents (via tool-section synopsis lines) must match exactly.
    Adding a command without documenting it (or vice versa) fails here."""
    assert set(main._parse_idea_help()) == set(main.COMMAND_HELP)


def test_help_renders_full_idea_section(capsys):
    main.cmd_help(types.SimpleNamespace(topic="status"))
    out = capsys.readouterr().out
    assert "### Status Tool" in out
    assert "Rationale" in out  # detail well beyond the COMMAND_HELP one-liner


def test_help_multi_command_family_shows_whole_section(capsys):
    # A command in a multi-command section renders the whole family.
    main.cmd_help(types.SimpleNamespace(topic="session_output_full_id"))
    out = capsys.readouterr().out
    assert "Copy Schedule, ID-of Schedule Tools" in out
    assert "dh_hl copy_schedule" in out  # sibling commands are visible


def test_help_strips_maintainer_only_lines(capsys):
    main.cmd_help(types.SimpleNamespace(topic="status"))
    out = capsys.readouterr().out
    assert "NOTE: [link" not in out
    assert "<!--" not in out


def test_parse_returns_empty_when_idea_md_missing():
    assert main._parse_idea_help("/no/such/idea.md") == {}


def test_help_falls_back_to_one_liner_without_idea_md(monkeypatch, capsys):
    monkeypatch.setattr(main, "_parse_idea_help", lambda *a, **k: {})
    main.cmd_help(types.SimpleNamespace(topic="status"))
    out = capsys.readouterr().out
    assert out.strip() == "status: " + main.COMMAND_HELP["status"]
