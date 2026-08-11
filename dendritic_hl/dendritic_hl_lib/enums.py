"""Enum types for dendritic_hl's small, fixed vocabularies.

Early code encoded every one of these fixed vocabularies as bare string
literals (`"success"`, `"neutral"`, `"enabled"`, `"improvement"`, ...) passed and
compared all over the codebase.  That is fragile: a typo is a silent bug, the
valid set is invisible at the use site, and nothing tells you where a value is a
real in-memory concept versus a wire string.

**Enum policy (see impl.md "Enum Policy").**  In memory, code passes and compares
ENUM MEMBERS, never the strings.  Strings live *only* on the wire -- on-disk
files, CLI arguments, and JSON output -- and we translate at that boundary:
`member.value` to serialize, `SomeEnum.from_wire(s)` (or the lenient try/except
`SomeEnum(s)`) to parse.

These are deliberately plain `enum.Enum`, NOT a `str` mixin: a member does *not*
silently behave as its string.  In particular `json.dumps(member)` raises
TypeError -- which is a feature, not a nuisance: it forces every serialization
boundary to spell out `.value`, so a wire format can never drift by accident.
`__str__` is overridden to the wire value purely for readable `print`/f-strings;
you still cannot `+`-concatenate a member with a str, so boundaries stay explicit.
"""

import enum

from .errors import DhHlError


class WireEnum(enum.Enum):
    """Base for the fixed-vocabulary enums whose `.value` is the canonical wire
    string (see the module docstring for the translation policy)."""

    def __str__(self):
        # Convenience for print()/f-strings; the value IS the wire form.  Does
        # not make the member a string: json.dumps still refuses it, and str
        # concatenation still raises, so the boundary stays explicit.
        return self.value

    @classmethod
    def from_wire(cls, s):
        """Parse a wire string to a member, or raise DhHlError naming the valid
        values.  Use where the input should already be valid (a CLI arg we mean
        to validate, our own disk writes).  For possibly-corrupt disk state that
        must degrade gracefully instead of erroring, catch ValueError from the
        plain `cls(s)` constructor and substitute a default (see e.g. Problem
        state / Commentary review loading in catalog.py)."""
        try:
            return cls(s)
        except ValueError:
            raise DhHlError("{!r} is not a valid {}; expected one of {}".format(
                s, cls.__name__, ", ".join(cls.wire_values())))

    @classmethod
    def wire_values(cls):
        """The valid wire strings in definition order (for CLI `choices=`, help
        text, and error messages)."""
        return tuple(m.value for m in cls)


class Result(WireEnum):
    """A schedule node's build result state (on disk: result.txt).

    Members are declared worst -> best; that definition order IS the ranking used
    by `catalog.best_result` (idea.md "Schedule Node State").  Absent result.txt
    reads as the worst value, UNKNOWN."""
    UNKNOWN = "unknown"
    CPP_ERROR = "c++ error"
    HALIDE_ERROR = "halide error"
    SUCCESS = "success"


class Review(WireEnum):
    """A commentary's review value (on disk: the `review` field of a comment
    JSON; on the CLI: `comment --review`).

    NEUTRAL/NEGATIVE/POSITIVE/LOST_INTEREST are the values a single commentary may
    carry.  MIXED is a *derived-only* value: a schedule/idea whose non-cancelled
    commentary contains both a positive and a negative derives MIXED (idea.md
    "Commentary State").  MIXED is never written to a commentary file and is
    rejected as a `comment --review` input; `COMMENTARY_REVIEWS` is the subset a
    commentary may take."""
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    POSITIVE = "positive"
    LOST_INTEREST = "lost_interest"
    MIXED = "mixed"


# The subset of Review a single commentary may carry (MIXED is derived-only).
COMMENTARY_REVIEWS = (Review.NEUTRAL, Review.NEGATIVE, Review.POSITIVE,
                      Review.LOST_INTEREST)


class ProblemState(WireEnum):
    """A problem object's enablement (on disk: state.txt).  MAIN is the single
    default problem for the cost tools; ENABLED problems (which includes the
    main) are the ones tested by default (idea.md "Problem Object State")."""
    ENABLED = "enabled"
    DISABLED = "disabled"
    MAIN = "main"


class SideLink(WireEnum):
    """A directional idea-side-link type (on disk: the presence of an empty file
    `idea/{A}/{value}/{B}`; on the CLI: the `add_idea_side_link` type arg)."""
    BORROWS_FROM = "borrows_from"
    SUPERSEDED_BY = "superseded_by"


class CostVerdict(WireEnum):
    """The verdict of a 2-way cost comparison (JSON output only -- never persisted
    to the catalog).  IMPROVEMENT means LHS is confidently cheaper than RHS;
    REGRESSION confidently dearer; UNKNOWN straddles zero or has too little data
    (idea.md "2-way Cost Comparison")."""
    IMPROVEMENT = "improvement"
    REGRESSION = "regression"
    UNKNOWN = "unknown"


class IdeaStateKind(WireEnum):
    """The kind of a parsed current_idea_state.txt (an in-memory classification;
    the on-disk encoding is the `dendritic_hl_root(...)`/`dendritic_hl_idea(...)`
    wrapper, handled separately in catalog.py).  NO_IDEA/IDEA are the two real
    states; MISSING (file absent) and CONFLICT (nothing / more than one parsed)
    are surfaced only when a caller needs a definite state."""
    MISSING = "missing"
    NO_IDEA = "no_idea"
    IDEA = "idea"
    CONFLICT = "conflict"
