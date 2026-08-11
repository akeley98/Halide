"""`dh_hl help <command>`: detailed per-command help sourced from idea.md, and
the doc<->code single-source drift guard."""

import types

from dendritic_hl_lib import main, guide_flag


def _both_guide_flags(callback):
    """Run *callback* once with the guide disabled and once with it enabled,
    restoring the flag afterward.  The doc<->code guards must hold either way."""
    original = guide_flag.enabled
    try:
        for f in (False, True):
            guide_flag.enabled = f
            callback()
    finally:
        guide_flag.enabled = original


def test_every_command_is_documented_in_idea_md():
    """The single-source guard: the set of CLI commands and the set of commands
    idea.md documents (via tool-section synopsis lines) must match exactly.
    Adding a command without documenting it (or vice versa) fails here.  Holds
    with the guide enabled (detail/examples present) and disabled (both gone)."""
    def callback():
        assert set(main._parse_idea_help()) == set(main.get_command_help_dict()), \
            guide_flag.enabled
    _both_guide_flags(callback)


def test_top_level_help_includes_tools_intro(capsys):
    """`dh_hl help` (no arg) lists commands AND prints the shared "## Tools"
    intro (argument conventions) from idea.md."""
    main.cmd_help(types.SimpleNamespace(topic=None))
    out = capsys.readouterr().out
    assert "dh_hl commands:" in out
    assert "`{...}` (curly brackets) means a mandatory argument." in out
    assert "`-` means stdin for any input file argument." in out
    # ends with the detail hint
    assert out.rstrip().endswith("for details.")


def test_parse_sections_returns_intro_and_mapping():
    def callback():
        intro, mapping = main._parse_idea_sections()
        assert "The tools are invoked with" in intro
        assert set(mapping) == set(main.get_command_help_dict()), guide_flag.enabled
    _both_guide_flags(callback)


def test_help_renders_full_idea_section(capsys):
    main.cmd_help(types.SimpleNamespace(topic="status"))
    out = capsys.readouterr().out
    assert "### Status Tool" in out
    assert "Rationale" in out  # detail well beyond the COMMAND_HELP one-liner


def test_help_multi_command_family_shows_whole_section(capsys):
    # A command in a multi-command section renders the whole family.
    main.cmd_help(types.SimpleNamespace(topic="commentary_full_id"))
    out = capsys.readouterr().out
    assert "### ID Translation Tools" in out
    assert "dh_hl schedule_full_id" in out  # sibling commands are visible


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
