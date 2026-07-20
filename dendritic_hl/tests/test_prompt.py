"""`dh_hl prompt`: assemble the main/sub agent prompt from prompt_common.md.

The parser is exercised on inline sources (so the real prompt_common.md stays
valid); cmd_prompt is exercised against the real file."""

import types

import pytest

from dendritic_hl_lib import prompts, tools
from dendritic_hl_lib.errors import DhHlError

_SRC = """\
common one

<!-- main -->
main only
<!-- end main -->

<!-- sub -->
sub only
<!-- end sub -->

common two
"""


def test_main_view_keeps_common_and_main_drops_sub():
    out = prompts.parse_prompt(_SRC, "main")
    assert "common one" in out and "common two" in out
    assert "main only" in out
    assert "sub only" not in out


def test_sub_view_keeps_common_and_sub_drops_main():
    out = prompts.parse_prompt(_SRC, "sub")
    assert "common one" in out and "common two" in out
    assert "sub only" in out
    assert "main only" not in out


def test_fences_and_comments_stripped_no_double_blanks():
    out = prompts.parse_prompt(_SRC, "main")
    assert "<!--" not in out and "-->" not in out
    assert "\n\n\n" not in out            # blank runs collapsed
    assert out.endswith("\n") and not out.startswith("\n")


def test_multiline_comment_is_stripped():
    src = "keep me\n<!--\n  a maintainer note\n  spanning lines\n-->\nkeep me too\n"
    out = prompts.parse_prompt(src, "main")
    assert "keep me" in out and "keep me too" in out
    assert "maintainer note" not in out


@pytest.mark.parametrize("bad", [
    "<!-- main -->\n<!-- sub -->\nx\n<!-- end sub -->\n<!-- end main -->\n",  # nested
    "<!-- end main -->\n",                          # unmatched close
    "<!-- main -->\nx\n",                           # unclosed at EOF
    "<!-- main -->\nx\n<!-- end sub -->\n",         # close audience mismatch
    "<!-- mian -->\nx\n<!-- end mian -->\n",        # unknown/typo'd audience tag
])
def test_malformed_fencing_raises(bad):
    with pytest.raises(DhHlError):
        prompts.parse_prompt(bad, "main")


# ---- the command (against the real prompt_common.md) ----------------------

def _ns(main=False, sub=False):
    return types.SimpleNamespace(main=main, sub=sub)


def test_cmd_prompt_main(capsys):
    tools.cmd_prompt(_ns(main=True))
    out = capsys.readouterr().out
    assert "main agent" in out
    assert "sub agent" not in out


def test_cmd_prompt_sub(capsys):
    tools.cmd_prompt(_ns(sub=True))
    out = capsys.readouterr().out
    assert "sub agent" in out
    assert "main agent" not in out


def test_cmd_prompt_requires_exactly_one_audience():
    with pytest.raises(DhHlError, match="exactly one"):
        tools.cmd_prompt(_ns())  # neither
    with pytest.raises(DhHlError, match="exactly one"):
        tools.cmd_prompt(_ns(main=True, sub=True))  # both


def test_load_prompt_missing_file_is_clean_error():
    with pytest.raises(DhHlError, match="cannot read prompt source"):
        prompts.load_prompt("main", "/no/such/prompt_common.md")
