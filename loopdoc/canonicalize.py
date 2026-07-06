#!/usr/bin/env python3
"""
Canonicalize Halide print_loop_nest() output for structural comparison.

WHY THIS EXISTS
---------------
The loopdoc experiment compares a `micro_halide` loop nest against the real
Halide loop nest. We do NOT want an exact byte comparison: Halide's output
carries cosmetic detail that is *not* the subject of the documentation
(auto-generated names, constant bounds, the precise spelling of a wrapper
function's name). We only want to compare the structure that the docs actually
teach: the produce/consume/store nesting, loop ordering and loop type, the set
of distinct functions, and which producer feeds which consumer.

This module parses the output into an indentation tree and re-emits a
NORMALIZED, still-human-readable tree, so a person (or the harness) can eyeball
or diff two canonical forms. It is deliberately readable, not hashed.

WHAT print_loop_nest ACTUALLY EMITS  (see ../src/PrintLoopNest.cpp)
-------------------------------------------------------------------
Five line shapes, nesting by 2-space indent:

    produce <fname>:
    consume <fname>:
    store <fname>:                      # only when store level != compute level
    <for_type> <var>[ in [lo, hi]][<device_api>]:
    <fname>(...) = ...

Halide has already elided a lot for us: leaf args/RHS are `(...) = ...`, `$n`
uniqueness suffixes are stripped, and bounds print only when constant.

NORMALIZATION POLICY  (intentionally lenient to start; tighten if false passes)
-------------------------------------------------------------------------------
KEPT (structural -- the doc's subject):
  * node kind: produce / consume / store / for / def
  * function identity, as a positional id F0, F1, ... assigned in order of first
    appearance. This preserves the count/distinctness of funcs and the
    producer<->consumer linkage, WITHOUT requiring the reader to predict the
    exact name Halide auto-picks for a wrapper/clone/rfactor function.
  * loop nesting depth and sibling order (this is loop order, e.g. reorder)
  * loop type (parallel/vectorized/unrolled/...) and device_api

NORMALIZED AWAY (cosmetic -- not what the docs teach):
  * constant loop bounds `in [lo, hi]`
  * loop variable names, ENTIRELY. Halide's loop names are compound and carry
    its internal split/rfactor lineage plus a global auto counter, e.g.
    `x.xi.v16`, `r15.r37`, `__outermost.v13`. Those segments are an
    implementation detail neither the docs teach nor the micro-author can
    predict. We therefore keep only loop TYPE and device, not the variable.
    Consequence: a pure serial `reorder` of two plain `for` loops is invisible
    here (both print just `for`). That is intentional -- name-based reorder
    checking is illusory anyway (the micro-author can name loops to match
    regardless of whether they were really exchanged), so reorder is better
    verified via an example whose reorder has a topological consequence.

The parser FAILS OPEN: any line it cannot classify raises ParseError rather
than silently guessing, so a surprising output gets a human's attention instead
of a misleading pass/fail.
"""

import re
import sys
from dataclasses import dataclass, field


class ParseError(Exception):
    pass


# The loop-type token printed by `operator<<(ostream&, ForType&)`
# (see ../src/IRPrinter.cpp). Pinning to this set keeps the parser fail-open:
# a surprising line that merely ends in ":" is NOT silently read as a loop.
_FOR_TYPES = ("for", "parallel", "unrolled", "vectorized",
              "extern", "gpu_block", "gpu_thread", "gpu_lane")

# Halide's print_loop_nest() path can emit free-form "Warning: ..." lines on
# stderr *before* the loop nest (e.g. splitting a var of an inlined func, or
# overwriting an existing compute_with). These are diagnostics, not loop-nest
# structure, and micro_halide does not reproduce them. Strip them so they don't
# trip the fail-open parser. (Targeted, human-authorized harness change.)
_WARNING_RE = re.compile(r"^Warning:")

_PRODUCE_RE = re.compile(r"^produce (?P<name>\S+):$")
_CONSUME_RE = re.compile(r"^consume (?P<name>\S+):$")
_STORE_RE = re.compile(r"^store (?P<name>\S+):$")
_DEF_RE = re.compile(r"^(?P<name>\S+)\(\.\.\.\) = \.\.\.$")
# for_type is one of the known tokens; var the second; optional " in [..]"
# bounds; optional "<device_api>" suffix; trailing colon.
_FOR_RE = re.compile(
    r"^(?P<ftype>" + "|".join(_FOR_TYPES) + r")\s+(?P<var>\S+?)"
    r"(?:\s+in \[(?P<bounds>.*?)\])?"
    r"(?P<dev><[^>]*>)?:$"
)

_INDENT = 2  # print_loop_nest indents by 2 spaces per level


@dataclass
class Node:
    kind: str  # "produce" | "consume" | "store" | "for" | "def"
    # For produce/consume/store/def: the raw function name.
    # For "for": unused.
    name: str = ""
    # For "for": the loop type token and (normalized) variable name + device.
    ftype: str = ""
    var: str = ""
    dev: str = ""
    children: list = field(default_factory=list)


def _classify(text: str) -> Node:
    if m := _PRODUCE_RE.match(text):
        return Node("produce", name=m["name"])
    if m := _CONSUME_RE.match(text):
        return Node("consume", name=m["name"])
    if m := _STORE_RE.match(text):
        return Node("store", name=m["name"])
    # def must be tried before "for": it ends in "= ..." not ":".
    if m := _DEF_RE.match(text):
        return Node("def", name=m["name"])
    if m := _FOR_RE.match(text):
        return Node("for", ftype=m["ftype"], var=m["var"], dev=m["dev"] or "")
    raise ParseError(f"Unrecognized loop-nest line: {text!r}")


def parse(output: str) -> list:
    """Parse print_loop_nest output into a forest of Nodes (top-level siblings)."""
    roots: list = []
    # Stack of (depth, node); node==None sentinel represents the virtual root.
    stack: list = [(-1, None)]
    for lineno, raw in enumerate(output.splitlines(), 1):
        if not raw.strip():
            continue
        if _WARNING_RE.match(raw):
            continue
        n_spaces = len(raw) - len(raw.lstrip(" "))
        if n_spaces % _INDENT != 0:
            raise ParseError(f"Line {lineno}: indent {n_spaces} not a multiple of {_INDENT}: {raw!r}")
        depth = n_spaces // _INDENT
        try:
            node = _classify(raw.strip())
        except ParseError as e:
            raise ParseError(f"Line {lineno}: {e}") from None
        # Pop until the top of stack is our parent (depth-1).
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if not stack:
            raise ParseError(f"Line {lineno}: bad indentation, no parent for depth {depth}")
        parent = stack[-1][1]
        (roots if parent is None else parent.children).append(node)
        stack.append((depth, node))
    return roots


def canonicalize(output: str) -> str:
    """Return the normalized, human-readable canonical form of a loop nest."""
    roots = parse(output)

    # Assign positional func ids in order of first appearance (document order).
    func_id: dict = {}

    def fid(name: str) -> str:
        if name not in func_id:
            func_id[name] = f"F{len(func_id)}"
        return func_id[name]

    lines: list = []

    def emit(node: Node, depth: int):
        pad = "  " * depth
        if node.kind in ("produce", "consume", "store"):
            lines.append(f"{pad}{node.kind} {fid(node.name)}")
        elif node.kind == "def":
            lines.append(f"{pad}def {fid(node.name)}")
        elif node.kind == "for":
            # Variable name dropped entirely (see module docstring); keep only
            # the loop type and device, which are structural and predictable.
            lines.append(f"{pad}{node.ftype}{node.dev}")
        for c in node.children:
            emit(c, depth + 1)

    for r in roots:
        emit(r, 0)
    return "\n".join(lines) + ("\n" if lines else "")


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path) as f:
        return f.read()


def _canonicalize_or_die(path: str) -> str:
    try:
        return canonicalize(_read(path))
    except ParseError as e:
        # Fail open: an unexpected line is a signal for a human, not a verdict.
        sys.stderr.write(f"canonicalize: {path}: {e}\n")
        raise SystemExit(2)


def main(argv: list) -> int:
    if len(argv) == 2:
        sys.stdout.write(_canonicalize_or_die(argv[1]))
        return 0
    if len(argv) == 4 and argv[1] == "--diff":
        a = _canonicalize_or_die(argv[2])
        b = _canonicalize_or_die(argv[3])
        if a == b:
            print("MATCH")
            return 0
        print("DIFFER\n")
        import difflib
        for line in difflib.unified_diff(
            a.splitlines(), b.splitlines(),
            fromfile=argv[2], tofile=argv[3], lineterm="",
        ):
            print(line)
        return 1
    sys.stderr.write(
        "usage:\n"
        "  canonicalize.py <file|->            # print canonical form\n"
        "  canonicalize.py --diff <a> <b>      # compare two outputs structurally\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
