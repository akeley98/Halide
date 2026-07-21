"""Assemble the main- or sub-agent prompt from the single-source prompt_common.md.

Mirrors the `dh_hl help` <- idea.md scheme: one human-edited source, code emits
the audience-specific view at runtime.  Content is COMMON (emitted to both
prompts) unless wrapped in an audience *fence* -- an HTML comment whose only word
is `main` or `sub`, closed by a matching `end main` / `end sub` comment:

    common text ...
    <!-- main -->
    main-only text ...
    <!-- end main -->

Fence lines and all other HTML comments are stripped from the output.  See the
FORMAT CONTRACT comment at the top of prompt_common.md.
"""

import os
import re

from .errors import DhHlError

# The harness source dir sits one level above the package dir; it holds the
# human-edited docs (prompt_common.md, idea.md, ...) and the detail/ + examples/
# directories.  A copy run detached from the repo won't find them -- callers get
# a clean DhHlError.
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROMPT_MD = os.path.join(_REPO_DIR, "prompt_common.md")

# Markdown docs concatenated after prompt_common.md to form the full prompt, in
# order, each with HTML comments removed (idea.md "Prompt Tools").
_PROMPT_DOCS = ("idea.md", "loopdoc.md", "adams_opus_scheduling_guide.md")

AUDIENCES = ("main", "sub")

# A fence line: an HTML comment whose only content is "<word>" or "end <word>".
_FENCE_RE = re.compile(r"^<!--\s*(end\s+)?(\w+)\s*-->$")

# Any HTML comment span (inline or multi-line); non-greedy, spanning newlines.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def parse_prompt(text, audience):
    """Return the *audience* ('main'/'sub') view of prompt-source *text*.

    Raises DhHlError on malformed fencing (nesting, an unmatched or dangling
    fence, or a fence-shaped comment naming an unknown audience -- single-word
    comments are reserved for fences, so a typo fails loudly rather than
    silently leaking a region into both prompts)."""
    assert audience in AUDIENCES
    out = []
    state = None          # None (common), or "main"/"sub" inside that fence
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
            if word not in AUDIENCES:
                raise DhHlError(
                    "prompt_common.md line {}: unknown audience tag {!r} "
                    "(expected a main/sub fence)".format(lineno, s))
            if not is_end:
                if state is not None:
                    raise DhHlError(
                        "prompt_common.md line {}: fence {!r} nested inside an "
                        "open {!r} fence".format(lineno, s, state))
                state = word
            else:
                if state != word:
                    raise DhHlError(
                        "prompt_common.md line {}: {!r} with no matching open "
                        "fence".format(lineno, s))
                state = None
            continue
        if s.startswith("<!--"):        # a non-fence comment -> strip it
            if "-->" not in s:
                in_comment = True        # multi-line comment; strip until close
            continue
        if state is None or state == audience:
            out.append(raw)
    if state is not None:
        raise DhHlError(
            "prompt_common.md: unclosed {!r} fence at end of file".format(state))
    return _collapse_blanks(out)


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

    *path* locates prompt_common.md; the other docs are read from the same
    directory (idea.md "Prompt Tools")."""
    repo_dir = os.path.dirname(os.path.abspath(path))
    parts = [parse_prompt(_read_source(path), audience)]
    for name in _PROMPT_DOCS:
        parts.append(strip_html_comments(_read_source(os.path.join(repo_dir, name))))
    # Single blank line between documents; exactly one trailing newline.
    return "".join(p.rstrip("\n") + "\n\n" for p in parts if p.strip()).rstrip("\n") + "\n"


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
    path = os.path.join(repo_dir, kind, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise DhHlError("cannot read {} file {!r}: {}".format(kind, name, e))
    if name.endswith(".md"):
        text = strip_html_comments(text)
    return text
