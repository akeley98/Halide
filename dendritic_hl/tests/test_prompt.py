"""`dh_hl prompt`: assemble the main/sub agent prompt from prompt_common.md.

The parser is exercised on inline sources (so the real prompt_common.md stays
valid); cmd_prompt is exercised against the real file."""

import types

import pytest

from dendritic_hl_lib import guide_flag, prompts, tools
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


def _both_guide_flags(callback):
    """Run *callback* once with the guide disabled and once with it enabled,
    restoring the in-process flag afterward."""
    original = guide_flag.enabled
    try:
        for f in (False, True):
            guide_flag.enabled = f
            callback()
    finally:
        guide_flag.enabled = original


# A guide-fenced source: the `<!-- guide -->` region is dropped from every view
# when the guide is disabled and kept (fence lines only removed) when enabled.
_GUIDE_SRC = """\
common head
<!-- guide -->
guide body
<!-- end guide -->
common tail
"""


def test_guide_region_dropped_when_disabled_kept_when_enabled():
    """The `guide` detail word is centrally driven by `guide_flag.enabled`, not by
    the caller's `remove_detail`: its region is dropped from every view when the
    guide is off and kept (fence lines stripped) when it is on."""
    def callback():
        for out in (prompts.parse_prompt(_GUIDE_SRC, "main"),
                    prompts.render_idea_help(_GUIDE_SRC)):
            assert "common head" in out and "common tail" in out
            assert "<!--" not in out
            assert ("guide body" in out) == guide_flag.enabled
    _both_guide_flags(callback)


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
    "<!-- help -->\n<!-- impl -->\nx\n<!-- end impl -->\n<!-- end help -->\n",  # detail nested
    "<!-- main -->\n<!-- impl -->\nx\n<!-- end impl -->\n<!-- end main -->\n",  # cross-axis nested
    "<!-- end main -->\n",                          # unmatched close
    "<!-- main -->\nx\n",                           # unclosed at EOF
    "<!-- main -->\nx\n<!-- end sub -->\n",         # close audience mismatch
    "<!-- mian -->\nx\n<!-- end mian -->\n",        # unknown/typo'd fence tag
])
def test_malformed_fencing_raises(bad):
    with pytest.raises(DhHlError):
        prompts.parse_prompt(bad, "main")


# ---- unified detail (help/impl) handling in the prompt view ---------------

_DETAIL_SRC = """\
common
<!-- help -->
help detail
<!-- end help -->
<!-- impl -->
impl note
<!-- end impl -->
tail
"""


def test_prompt_view_drops_both_detail_axes():
    """`parse_prompt` (prompt view) drops help AND impl regions in either file,
    keeping only common text."""
    out = prompts.parse_prompt(_DETAIL_SRC, "main")
    assert "common" in out and "tail" in out
    assert "help detail" not in out and "impl note" not in out


def test_help_view_keeps_help_and_both_audiences_drops_impl():
    """`render_idea_help` keeps help content and BOTH audiences, drops only impl."""
    src = _DETAIL_SRC + _SRC
    out = prompts.render_idea_help(src)
    assert "help detail" in out          # help kept
    assert "impl note" not in out        # impl dropped
    assert "main only" in out and "sub only" in out   # audience-neutral


def test_error_message_names_source_file():
    with pytest.raises(DhHlError, match="idea.md line"):
        prompts.render_fenced("<!-- bogus -->\n", audience=None,
                              remove_detail=("impl",), source="idea.md")


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


def test_load_doc_real_hpp_example_is_verbatim():
    """A real examples/*.hpp resolves as-is: the .cpp fallback must NOT rewrite an
    explicit, resolvable .hpp name (idea.md "Prompt Tools")."""
    import os
    name = "compute_at_inline_dependence.hpp"
    raw = open(os.path.join(prompts._REPO_DIR, "examples", name),
               encoding="utf-8").read()
    assert prompts.load_doc("examples", name) == raw


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


# ---- load_doc: quiet extension fallback (idea.md "Prompt Tools") -----------

def test_load_doc_appends_md_for_detail_when_missing():
    # 'specialize' (no extension) quietly falls back to 'specialize.md'.
    out = prompts.load_doc("detail", "specialize")
    assert out == prompts.load_doc("detail", "specialize.md")


def test_load_doc_appends_cpp_for_examples_when_missing():
    out = prompts.load_doc("examples", "tile_basic")
    assert out == prompts.load_doc("examples", "tile_basic.cpp")


def _doc_repo(tmp_path, kind, files):
    d = tmp_path / kind
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return str(tmp_path)


def test_load_doc_explicit_hpp_is_not_rewritten(tmp_path):
    """The fallback must NOT blindly append .md: an explicit, resolvable name
    (e.g. a detail `.hpp`) is honored verbatim, even when a same-stem `.md`
    exists alongside it."""
    repo = _doc_repo(tmp_path, "detail", {
        "helper.hpp": "#pragma once  <!-- keep -->\n",
        "helper.md": "# the markdown one\n"})
    out = prompts.load_doc("detail", "helper.hpp", repo_dir=repo)
    assert out == "#pragma once  <!-- keep -->\n"  # verbatim: NOT the .md, NOT stripped


def test_load_doc_explicit_extensionless_wins_over_default(tmp_path):
    """An explicit name that resolves as-is is never rewritten, even if
    `name + default_ext` also exists."""
    repo = _doc_repo(tmp_path, "detail", {
        "note": "raw note\n", "note.md": "# other\n"})
    assert prompts.load_doc("detail", "note", repo_dir=repo) == "raw note\n"


def test_load_doc_fallback_error_names_both_tries(tmp_path):
    repo = _doc_repo(tmp_path, "detail", {})
    with pytest.raises(DhHlError, match=r"cannot read detail file 'ghost'.*also tried 'ghost\.md'"):
        prompts.load_doc("detail", "ghost", repo_dir=repo)


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


def test_real_prompt_common_impl_section_absent_from_both_prompts():
    """The `<!-- impl -->` "Side Note: Seed Ideas Found To Be Harmful" section in
    prompt_common.md must not leak into either assembled prompt."""
    for audience in ("main", "sub"):
        out = prompts.load_prompt(audience)
        assert "Seed Ideas Found To Be Harmful" not in out


def test_real_idea_main_section_is_audience_specialized():
    """idea.md's `<!-- main -->` "Main Agent Default Session Behavior" section
    appears in the main prompt but NOT the sub prompt"""
    marker = "Main Agent Default Session Behavior"
    assert marker in prompts.load_prompt("main")
    assert marker not in prompts.load_prompt("sub")


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


# ---- guide ablation through the real CLI ----------------------------------
#
# The DENDRITIC_HL_GUIDE_ENABLED env var is read at import time by
# dendritic_hl_lib.guide_flag, so it can only be exercised in a child process --
# hence these run_cli (subprocess) tests, not in-process ones.

# Markers that only appear when the guide is enabled: content from the appended
# loopdoc / scheduling guide, plus the guide-fenced idea.md tool section.
_GUIDE_MARKERS = (
    "how Halide turns",                # loopdoc.md body
    "A Concise Guide to CPU Scheduling",  # adams_opus_scheduling_guide.md title
    "Supplemental Document Tools",     # guide-fenced idea.md section
)


def test_cli_prompt_includes_guide_content_by_default(run_cli):
    """With the guide enabled (the default, no env override), `dh_hl prompt`
    appends the loopdoc + scheduling guide and keeps the guide-fenced idea.md
    tool section."""
    r = run_cli("prompt", "--main")
    assert r.returncode == 0, r.stderr
    for marker in _GUIDE_MARKERS:
        assert marker in r.stdout, marker


def test_cli_prompt_omits_guide_content_when_disabled(run_cli):
    """With DENDRITIC_HL_GUIDE_ENABLED=0, the assembled prompt drops the appended
    loopdoc / scheduling guide and the guide-fenced idea.md content, while still
    emitting the core prompt."""
    r = run_cli("prompt", "--main", env={"DENDRITIC_HL_GUIDE_ENABLED": "0"})
    assert r.returncode == 0, r.stderr
    assert "Dendritic Halide Harness" in r.stdout   # core prompt still present
    for marker in _GUIDE_MARKERS:
        assert marker not in r.stdout, marker


def test_cli_detail_examples_fail_and_silent_when_guide_disabled(run_cli):
    """With the guide disabled the `detail`/`examples` subcommands do not exist:
    invoking them exits non-zero and writes nothing to stdout (idea.md
    "Supplemental Document Tools")."""
    env = {"DENDRITIC_HL_GUIDE_ENABLED": "0"}
    for cmd in ("detail", "examples"):
        r = run_cli(cmd, "specialize.md", env=env)
        assert r.returncode != 0, cmd
        assert r.stdout == "", (cmd, r.stdout)


def test_cli_detail_examples_work_when_guide_enabled(run_cli):
    """The complementary case: with the guide enabled the subcommands resolve and
    print their document to stdout."""
    r = run_cli("detail", "specialize.md")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip()
    r = run_cli("examples", "tile_basic.cpp")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip()


# The secret env var must not leak to harness users: it is documented only in an
# idea.md `impl` block (dropped from every prompt/help view) so agents cannot
# discover and circumvent the ablation.  These guard against it slipping into any
# user-visible output.  (Brittle to future doc edits, but trivially re-fixable.)
_GUARD_WORD = "DENDRITIC_HL_GUIDE_ENABLED"


def test_guard_env_var_never_leaks_into_prompt():
    """The `DENDRITIC_HL_GUIDE_ENABLED` name appears in neither the main nor the
    sub prompt, with the guide enabled or disabled."""
    def callback():
        for audience in ("main", "sub"):
            assert _GUARD_WORD not in prompts.load_prompt(audience)
    _both_guide_flags(callback)


def test_guard_env_var_never_leaks_into_served_docs():
    """No file served by `detail`/`examples` mentions the guard env var (checked
    through `load_doc`, the exact output path the CLI prints)."""
    import os
    for kind in ("detail", "examples"):
        for name in sorted(os.listdir(os.path.join(prompts._REPO_DIR, kind))):
            assert _GUARD_WORD not in prompts.load_doc(kind, name), (kind, name)


def test_cli_prompt_schedule_suggestion_bullet_iff_guide_enabled(run_cli):
    """The guide-fenced prompt_common.md bullet promising the scheduling guide
    appears in `dh_hl prompt` exactly when the guide is enabled."""
    marker = "A guide giving suggestions on how to produce a Halide schedule"
    assert marker in run_cli("prompt", "--main").stdout
    r = run_cli("prompt", "--main", env={_GUARD_WORD: "0"})
    assert marker not in r.stdout
