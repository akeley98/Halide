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


# ---- strip_html_comments --------------------------------------------------

def test_strip_html_comments_inline_and_multiline():
    src = "a <!-- inline --> b\n<!--\n block note\n-->\nc\n"
    out = prompts.strip_html_comments(src)
    assert "<!--" not in out and "-->" not in out
    assert "block note" not in out
    assert "b" in out and "c" in out
    assert "\n\n\n" not in out  # blank runs collapsed


# ---- load_prompt: full assembly against a synthetic repo ------------------

def _make_repo(tmp_path, common):
    (tmp_path / "prompt_common.md").write_text(common)
    (tmp_path / "idea.md").write_text("# idea doc\n<!-- strip me -->\nidea body\n")
    (tmp_path / "loopdoc.md").write_text("# loopdoc\nloop body\n")
    (tmp_path / "adams_opus_scheduling_guide.md").write_text("# adams\nadams body\n")
    return str(tmp_path / "prompt_common.md")


def test_load_prompt_concatenates_docs_in_order_with_fencing(tmp_path):
    path = _make_repo(tmp_path, _SRC)
    out = prompts.load_prompt("main", path)
    # prompt_common is audience-fenced (main view keeps main, drops sub)...
    assert "common one" in out and "main only" in out and "sub only" not in out
    # ...then the three docs follow, in order, with comments stripped.
    for marker in ("idea body", "loop body", "adams body"):
        assert marker in out
    assert out.index("idea body") < out.index("loop body") < out.index("adams body")
    assert "<!--" not in out and "strip me" not in out


def test_load_prompt_sub_view_uses_sub_fence(tmp_path):
    path = _make_repo(tmp_path, _SRC)
    out = prompts.load_prompt("sub", path)
    assert "sub only" in out and "main only" not in out


def test_load_prompt_missing_common_is_clean_error():
    with pytest.raises(DhHlError, match="cannot read prompt source"):
        prompts.load_prompt("main", "/no/such/prompt_common.md")


def test_load_prompt_missing_doc_is_clean_error(tmp_path):
    path = _make_repo(tmp_path, _SRC)
    import os
    os.remove(str(tmp_path / "loopdoc.md"))
    with pytest.raises(DhHlError, match="cannot read prompt source"):
        prompts.load_prompt("main", path)


# ---- load_doc: detail / examples ------------------------------------------

def test_load_doc_markdown_strips_comments():
    out = prompts.load_doc("detail", "specialize.md")
    assert out.strip()
    assert "<!--" not in out


def test_load_doc_cpp_is_verbatim():
    import os
    raw = open(os.path.join(prompts._REPO_DIR, "examples", "tile_basic.cpp"),
               encoding="utf-8").read()
    assert prompts.load_doc("examples", "tile_basic.cpp") == raw


@pytest.mark.parametrize("bad", ["../idea.md", "sub/x.md", "/etc/passwd", "a/b/c"])
def test_load_doc_rejects_directory_components(bad):
    with pytest.raises(DhHlError, match="must be a bare filename"):
        prompts.load_doc("detail", bad)


def test_load_doc_missing_file_is_clean_error():
    with pytest.raises(DhHlError, match="cannot read detail file"):
        prompts.load_doc("detail", "nonesuch.md")


def test_load_doc_dotdot_is_clean_directory_error():
    # ".." passes the split check (empty head) but is a directory -> clean error,
    # NOT a traversal into the repo root.
    with pytest.raises(DhHlError, match="cannot read detail file"):
        prompts.load_doc("detail", "..")


# ---- the commands ---------------------------------------------------------

def _ns(main=False, sub=False):
    return types.SimpleNamespace(main=main, sub=sub)


def test_cmd_prompt_smoke(capsys):
    """Runs against the real docs: assembles without error and includes the
    prompt_common title (fencing/ordering are covered by the synthetic tests)."""
    tools.cmd_prompt(_ns(main=True))
    out = capsys.readouterr().out
    assert "Dendritic Halide Harness" in out
    assert len(out) > 1000  # the three appended docs are substantial


def test_cmd_prompt_requires_exactly_one_audience():
    with pytest.raises(DhHlError, match="exactly one"):
        tools.cmd_prompt(_ns())  # neither
    with pytest.raises(DhHlError, match="exactly one"):
        tools.cmd_prompt(_ns(main=True, sub=True))  # both


def test_cmd_detail_and_examples(capsys):
    tools.cmd_detail(types.SimpleNamespace(name="specialize.md"))
    assert capsys.readouterr().out.strip()
    tools.cmd_examples(types.SimpleNamespace(name="tile_basic.cpp"))
    assert capsys.readouterr().out.strip()
