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

# prompt_common.md sits one level above the package dir (like idea.md); a copy
# run detached from the repo won't find it -- callers get a clean DhHlError.
_PROMPT_MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "prompt_common.md")

AUDIENCES = ("main", "sub")

# A fence line: an HTML comment whose only content is "<word>" or "end <word>".
_FENCE_RE = re.compile(r"^<!--\s*(end\s+)?(\w+)\s*-->$")


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


def load_prompt(audience, path=_PROMPT_MD):
    """Assemble the *audience* prompt from prompt_common.md on disk."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise DhHlError("cannot read prompt source {!r}: {}".format(path, e))
    return parse_prompt(text, audience)
