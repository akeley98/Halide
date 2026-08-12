"""Assemble the agent prompt and `dh_hl help` docs from single-source Markdown.

Both `prompt_common.md` and `idea.md` (and the guide docs) are human-edited
sources from which the code emits an audience-, detail-, and harness-specific
view at runtime.  Content is COMMON (emitted everywhere) unless wrapped in a
*fence* -- an HTML comment whose only word is one of seven, on three orthogonal
axes:

    common text ...
    <!-- main -->      audience axis: main / sub
    main-only text ...
    <!-- end main -->
    <!-- impl -->      detail axis: help / impl
    implementer note ...
    <!-- end impl -->
    <!-- guide -->     detail axis: guide (dropped from every view when the
    guide-ablation text ...   guide is disabled; see `guide_flag`)
    <!-- end guide -->
    <!-- harness_T --> harness axis: harness_T / harness_F (kept in the full
    harness-only text ...     prompt vs. `--guide-only`; see `load_guide_only`)
    <!-- end harness_T -->

`render_fenced` is the single engine (see its docstring); `parse_prompt` is the
prompt view (pick an audience, drop both detail axes) and `render_idea_help` is
the `dh_hl help` view (keep both audiences, drop impl, keep help).  Fence lines
and all other HTML comments are stripped from the output.  See the FORMAT
CONTRACT comments atop prompt_common.md and above "# Tools" in idea.md.
"""

import os
import re

from .errors import DhHlError
from . import guide_flag

# The harness source dir sits one level above the package dir; it holds the
# human-edited docs (prompt_common.md, idea.md, ...) and the detail/ + examples/
# directories.  A copy run detached from the repo won't find them -- callers get
# a clean DhHlError.
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROMPT_MD = os.path.join(_REPO_DIR, "prompt_common.md")

# Markdown docs concatenated after prompt_common.md to form the full prompt, in
# order, each with HTML comments removed (idea.md "Prompt Tools").
_PROMPT_DOCS = ("idea.md", "loopdoc.md", "adams_opus_scheduling_guide.md")

# The extension `load_doc` quietly appends as a fallback when a bare name does
# not resolve (idea.md "Prompt Tools"): some docs cite these files without their
# extension.  Only a fallback -- an explicit, resolvable name (e.g. a detail
# `.hpp`) is honored as-is and never rewritten.
_DEFAULT_DOC_EXT = {"detail": ".md", "examples": ".cpp"}

AUDIENCES = ("main", "sub")
_DETAIL_WORDS = ("help", "impl")

# The `guide` word is a third detail-axis tag, but unlike help/impl its removal is
# not chosen per-view by callers: a `<!-- guide -->` region is dropped from EVERY
# view when the guide ablation is active (`guide_flag.enabled` is False) and kept
# (fence lines only removed) otherwise.  `render_fenced` folds it into
# `remove_detail` centrally, so no caller passes it (idea.md "Supplemental
# Document Tools").
_GUIDE_WORD = "guide"

# The harness axis (`harness_T`/`harness_F`) distinguishes the two ways the guide
# docs are emitted: embedded in the full agent prompt (WITH the harness, so
# `harness=True`) versus `dh_hl prompt --guide-only`, which prints only loopdoc.md
# + adams_opus_scheduling_guide.md as a standalone guide (NO harness, so
# `harness=False`).  A `<!-- harness_T -->` region is kept only in the full prompt
# and dropped from `--guide-only`; `<!-- harness_F -->` is the reverse.  So e.g.
# every mention of a `dh_hl` tool in the scheduling guide lives in a harness_T
# block, since it is meaningless without the harness (idea.md "Harness Prompt
# Tools -- Implementation Details").  `harness=None` keeps BOTH (the default; used
# by views with no harness fences, like `dh_hl help`).
_HARNESS_WORDS = ("harness_T", "harness_F")
_HARNESS_KEEP = {True: "harness_T", False: "harness_F"}

# The recognized fence words fall on three axes:
#   * Audience axis (`main`/`sub`): a region is kept only if its word matches the
#     view's target audience; `audience=None` keeps BOTH audiences (the `help`
#     view, which is audience-neutral).
#   * Detail axis (`help`/`impl`, plus `guide`): `<!-- help -->` wraps text meant
#     for `dh_hl help <command>` but too verbose for the prompt; `<!-- impl -->`
#     wraps implementer-only notes wanted by neither view.  A detail word in
#     `remove_detail` drops its whole region; a recognized detail word NOT in
#     `remove_detail` drops just its fence lines (keeping the content).  `guide`
#     is folded into `remove_detail` centrally (see `render_fenced`).
#   * Harness axis (`harness_T`/`harness_F`): selected by the `harness` flag, as
#     above.
# Fences do NOT nest -- at most one is open at a time, of any word (a maintainer
# note wanted inside an open region is written as a plain multi-word HTML comment,
# which is stripped from every view).  This single engine is shared by
# `prompt_common.md`, `idea.md`, and the guide docs (idea.md "Prompt Tools", "Help
# Tool -- Implementation Details", and the FORMAT CONTRACTs in both files).
_FENCE_WORDS = frozenset(AUDIENCES + _DETAIL_WORDS + (_GUIDE_WORD,) + _HARNESS_WORDS)

# A fence line: an HTML comment whose only content is "<word>" or "end <word>".
_FENCE_RE = re.compile(r"^<!--\s*(end\s+)?(\w+)\s*-->$")

# Any HTML comment span (inline or multi-line); non-greedy, spanning newlines.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def render_fenced(text, *, audience, remove_detail, harness=None,
                  source="prompt_common.md"):
    """Render the (audience, detail, harness) view of fence-source *text*.

    *audience* is 'main'/'sub' (keep that audience, drop the other) or None (keep
    both audiences).  *remove_detail* is the set of detail words ('help'/'impl')
    whose regions are dropped; `guide` is added to it automatically whenever the
    guide ablation is active (`guide_flag.enabled` is False).  *harness* is True
    (keep `harness_T`, drop `harness_F`), False (the reverse -- the `--guide-only`
    view), or None (keep both).  Fence lines and all other HTML comments are
    stripped; blank runs left behind are collapsed.

    Raises DhHlError on malformed fencing: any nesting, an unmatched or dangling
    fence, or a fence-shaped comment naming an unknown word (single-word comments
    are reserved for fences, so a typo fails loudly rather than silently leaking a
    region).  *source* names the file in error messages."""
    assert audience is None or audience in AUDIENCES
    assert harness is None or isinstance(harness, bool)
    # The guide ablation removes every `<!-- guide -->` region from all views; when
    # the guide is enabled the word is a recognized-but-not-removed detail tag, so
    # only its fence lines drop.  Callers never pass `guide` themselves.
    if not guide_flag.enabled:
        remove_detail = frozenset(remove_detail) | {_GUIDE_WORD}
    kept = []
    state = None          # the single open fence word, or None (common)
    in_comment = False    # inside a multi-line <!-- ... --> block (stripped)
    for lineno, raw in enumerate(text.split("\n"), 1):
        s = raw.strip()
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        m = _FENCE_RE.match(s)
        if m:
            is_end, word = bool(m.group(1)), m.group(2)
            if word not in _FENCE_WORDS:
                raise DhHlError(
                    "{} line {}: unknown fence tag {!r} (expected one of "
                    "main/sub/help/impl/guide/harness_T/harness_F)".format(
                        source, lineno, s))
            if not is_end:
                if state is not None:
                    raise DhHlError(
                        "{} line {}: fence {!r} nested inside an open {!r} "
                        "fence".format(source, lineno, s, state))
                state = word
            else:
                if state != word:
                    raise DhHlError(
                        "{} line {}: {!r} with no matching open fence".format(
                            source, lineno, s))
                state = None
            continue
        if s.startswith("<!--") and "-->" not in s:
            in_comment = True            # multi-line comment; strip until close
            continue
        drop = (state in AUDIENCES and audience is not None and state != audience) \
            or (state in _HARNESS_WORDS and harness is not None
                and state != _HARNESS_KEEP[harness]) \
            or (state in remove_detail)
        if not drop:
            kept.append(raw)             # may still hold an inline comment
    if state is not None:
        raise DhHlError(
            "{}: unclosed {!r} fence at end of file".format(source, state))
    # A final pass drops any inline / mid-line comments the line loop kept.
    return _collapse_blanks(_HTML_COMMENT_RE.sub("", "\n".join(kept)).split("\n"))


def parse_prompt(text, audience, source="prompt_common.md", harness=None):
    """The prompt view of *audience* ('main'/'sub'): keep that audience's regions,
    drop the other's, and drop BOTH detail axes (help + impl).  Shared by
    `prompt_common.md` and `idea.md` (idea.md "Prompt Tools"); *source* names the
    file in error messages.  *harness* selects the harness axis (the assembled
    prompt passes True -- it IS the harness prompt)."""
    return render_fenced(text, audience=audience, remove_detail=_DETAIL_WORDS,
                         harness=harness, source=source)


def _render_guide_doc(text, harness, source):
    """The prompt view of a guide doc (loopdoc.md / adams_opus_scheduling_guide.md):
    audience-neutral, both detail axes dropped, with the *harness* axis selected
    (True in the full prompt, False for `--guide-only`).  Guide docs carry no
    audience/help/impl fences today, but running them through the one engine keeps
    the harness fences honest and validated (idea.md "Harness Prompt Tools")."""
    return render_fenced(text, audience=None, remove_detail=_DETAIL_WORDS,
                         harness=harness, source=source)


def _join_parts(parts):
    """Join processed docs with a single blank line between them and exactly one
    trailing newline; skip wholly-blank parts."""
    return "".join(p.rstrip("\n") + "\n\n" for p in parts if p.strip()
                   ).rstrip("\n") + "\n"


def _collapse_blanks(lines):
    """Join *lines*, collapsing the blank-line runs left by dropped fences /
    other-audience blocks to a single blank, with no leading/trailing blanks."""
    result = []
    for ln in lines:
        if ln.strip() == "" and (not result or result[-1].strip() == ""):
            continue
        result.append(ln)
    while result and result[-1].strip() == "":
        result.pop()
    return "\n".join(result) + "\n" if result else ""


def strip_html_comments(text):
    """Remove every HTML comment span (inline or multi-line) from *text* and
    collapse the blank-line runs left behind.  This is the sole processing
    applied to the Markdown docs concatenated into the prompt (and to Markdown
    files served by `detail`/`examples`)."""
    return _collapse_blanks(_HTML_COMMENT_RE.sub("", text).split("\n"))


def render_idea_help(text):
    """The idea.md view rendered by `dh_hl help`: impl regions removed, help
    regions kept, and BOTH audiences kept (`dh_hl help` is audience-neutral;
    idea.md "Help Tool -- Implementation Details").  Fence tracking runs over the
    *whole* text before section parsing, so a region that spans a heading
    boundary (e.g. an impl region wrapping an entire "### ... Implementation
    Details" section) is handled correctly."""
    return render_fenced(text, audience=None, remove_detail=("impl",),
                         source="idea.md")


def _read_source(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise DhHlError("cannot read prompt source {!r}: {}".format(path, e))


def load_prompt(audience, path=_PROMPT_MD):
    """Assemble the full *audience* prompt: prompt_common.md (audience-fenced,
    comments stripped) followed by idea.md, loopdoc.md, and the Adams/Opus
    scheduling guide (each with HTML comments stripped), in that order.
    The loopdoc.md and Adams/Opus scheduling guide are disabled if guide_flag.enabled=False.

    *path* locates prompt_common.md; the other docs are read from the same
    directory (idea.md "Prompt Tools").  This is the WITH-harness view, so every
    doc is rendered with `harness=True` (keep `harness_T`, drop `harness_F`)."""
    repo_dir = os.path.dirname(os.path.abspath(path))
    parts = [parse_prompt(_read_source(path), audience, harness=True)]
    for name in _PROMPT_DOCS:
        src = _read_source(os.path.join(repo_dir, name))
        # idea.md runs through the same audience+detail fence engine as
        # prompt_common.md (its `<!-- main -->` section is audience-fenced and its
        # help/impl detail regions are dropped); the guide docs are audience-neutral
        # but carry harness fences, so they go through `_render_guide_doc`.  All are
        # harness=True here (the assembled prompt IS the harness prompt).
        if name == "idea.md":
            parts.append(parse_prompt(src, audience, source="idea.md",
                                      harness=True))
        elif guide_flag.enabled:
            parts.append(_render_guide_doc(src, harness=True, source=name))
    return _join_parts(parts)


# The subset of _PROMPT_DOCS emitted by `--guide-only`: the two standalone guide
# documents, in order, with NO prompt_common.md / idea.md harness context.
_GUIDE_ONLY_DOCS = ("loopdoc.md", "adams_opus_scheduling_guide.md")


def load_guide_only(path=_PROMPT_MD):
    """Assemble the `dh_hl prompt --guide-only` output: loopdoc.md followed by the
    Adams/Opus scheduling guide, and NOTHING else (no prompt_common.md / idea.md).
    This is the NO-harness view, so each doc is rendered with `harness=False`
    (keep `harness_F`, drop the `harness_T` blocks that only make sense alongside
    the harness).  The guide ablation must be OFF for this to be meaningful, so the
    guide's presence is asserted (idea.md "Harness Prompt Tools")."""
    assert guide_flag.enabled, "--guide-only requires the guide enabled"
    repo_dir = os.path.dirname(os.path.abspath(path))
    parts = [_render_guide_doc(_read_source(os.path.join(repo_dir, name)),
                               harness=False, source=name)
             for name in _GUIDE_ONLY_DOCS]
    return _join_parts(parts)


def load_doc(kind, name, repo_dir=_REPO_DIR):
    """Return the text of the *name* file in the *kind* ('detail'/'examples')
    directory of the harness source repo, with HTML comments stripped for
    Markdown files.  *name* must be a plain filename with no directory
    component -- the sole sanitization is requiring `os.path.split(name)[0]`
    to be empty, which rejects any path separator (so `../`, `sub/x`, absolute
    paths, and a trailing slash all fail); a bare `.`/`..` passes that check but
    then fails cleanly as a directory-read error below."""
    if os.path.split(name)[0] != "":
        raise DhHlError(
            "invalid {} name {!r}: must be a bare filename with no directory "
            "component".format(kind, name))
    # Try the name as given; only if it is NOT FOUND, retry with the kind's
    # default extension appended (idea.md "Prompt Tools").  This is a pure
    # fallback: an explicit resolvable name wins, so a non-default extension
    # (e.g. a detail `.hpp`) is never rewritten to `.md`.  Non-not-found errors
    # (a directory read for a bare `.`/`..`, permissions) surface immediately and
    # are NOT retried.
    default_ext = _DEFAULT_DOC_EXT.get(kind, "")
    candidates = [name]
    if default_ext and not name.endswith(default_ext):
        candidates.append(name + default_ext)
    text = resolved = None
    for cand in candidates:
        try:
            with open(os.path.join(repo_dir, kind, cand), "r",
                      encoding="utf-8") as f:
                text = f.read()
            resolved = cand
            break
        except FileNotFoundError:
            continue
        except OSError as e:
            raise DhHlError("cannot read {} file {!r}: {}".format(kind, cand, e))
    if resolved is None:
        also = ("" if len(candidates) == 1
                else " (also tried {!r})".format(candidates[-1]))
        raise DhHlError(
            "cannot read {} file {!r}: no such file{}".format(kind, name, also))
    if resolved.endswith(".md"):
        text = strip_html_comments(text)
    return text
