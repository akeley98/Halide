#!/usr/bin/env python3
"""
Summarize micro-agent activity from tool_audit.jsonl (written by log_tool_use.py).

Purpose: after a micro-agent run, answer "did the experiment proceed as
intended?" without scrubbing the whole transcript by hand. It groups tool calls
by agent run (agent_id) and reports, per run:

  * a tool-call count breakdown
  * HARD integrity FLAGS -- things that invalidate the run if present:
      - reading Halide source (../src, src/) or src_doc/
      - any `git commit`
      - peeking at main_agent_todo.md
  * INFO signals to eyeball (not necessarily wrong):
      - whether it read loopdoc.md (engagement -- a pass means nothing if not)
      - edits to loopdoc.md (allowed ONLY as <!-- --> comments)
      - reads/edits of micro_halide (the actual work)
      - invocations of the full harness / test.sh

Usage:
    python3 review_micro.py            # summarize every micro-agent run in the log
    python3 review_micro.py --all      # include non-micro agents too
    python3 review_micro.py --agent ID # only the run with this agent_id
    python3 review_micro.py --last     # only the most recent micro-agent run
"""

import json
import os
import re
import sys
from collections import Counter, OrderedDict

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_audit.jsonl")

# Forbidden-access patterns over the tool argument (file path or bash command).
FORBIDDEN = OrderedDict([
    ("Halide source (src/)", re.compile(r"(^|[/\s.])src/")),
    ("src_doc/", re.compile(r"src_doc\b")),
    ("git commit", re.compile(r"\bgit\s+commit\b")),
    ("main agent todo", re.compile(r"main_agent_todo\.md\b")),
])

INFO = OrderedDict([
    ("read loopdoc.md", re.compile(r"loopdoc\.md")),
    ("touched micro_halide", re.compile(r"micro_halide")),
    ("ran harness/test.sh", re.compile(r"\btest\.sh\b|harness\.py|test_all")),
])


def load(path):
    if not os.path.exists(path):
        sys.exit(f"no audit log at {path} (has a logged micro-agent run yet?)")
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def summarize_run(agent_id, rows):
    print(f"\n{'=' * 70}")
    label = rows[0].get("agent", "?")
    span = f"{rows[0].get('ts','?')} .. {rows[-1].get('ts','?')}"
    print(f"agent={label}  agent_id={agent_id}  calls={len(rows)}  [{span}]")
    print("=" * 70)

    counts = Counter(r.get("tool", "?") for r in rows)
    print("tool calls: " + ", ".join(f"{t}={n}" for t, n in counts.most_common()))

    def hits(pattern):
        return [r for r in rows if r.get("arg") and pattern.search(r["arg"])]

    flagged = False
    print("\nintegrity:")
    for name, pat in FORBIDDEN.items():
        h = hits(pat)
        if h:
            flagged = True
            print(f"  [FLAG] {name}: {len(h)} call(s)")
            for r in h[:5]:
                print(f"           {r.get('tool')}: {r.get('arg')}")
            if len(h) > 5:
                print(f"           ... and {len(h) - 5} more")
    if not flagged:
        print("  [OK]   no forbidden accesses detected")

    print("\ninfo:")
    for name, pat in INFO.items():
        n = len(hits(pat))
        mark = "yes" if n else "NO"
        print(f"  {name}: {mark}" + (f" ({n})" if n else ""))
    if not hits(INFO["read loopdoc.md"]):
        print("  [WARN] micro-agent never read loopdoc.md -- a passing test proves little")

    return flagged


def main(argv):
    rows = load(LOG_PATH)

    want_all = "--all" in argv
    only_last = "--last" in argv
    only_agent = None
    if "--agent" in argv:
        only_agent = argv[argv.index("--agent") + 1]

    # Group preserving first-seen order.
    runs = OrderedDict()
    for r in rows:
        if not want_all and r.get("agent") != "micro-halide":
            continue
        runs.setdefault(r.get("agent_id", "-"), []).append(r)

    if only_agent:
        runs = {only_agent: runs.get(only_agent, [])}
    elif only_last and runs:
        last_id = list(runs)[-1]
        runs = {last_id: runs[last_id]}

    if not runs or all(not v for v in runs.values()):
        print("no matching micro-agent activity in the log.")
        print("(spawn micro-agents with subagent_type 'micro-halide' so they are logged.)")
        return 0

    any_flag = False
    for agent_id, run in runs.items():
        if run:
            any_flag |= summarize_run(agent_id, run)

    print(f"\n{'=' * 70}")
    print("RESULT: " + ("INTEGRITY FLAGGED -- review/discard affected run(s)"
                        if any_flag else "no integrity flags across reviewed runs"))
    return 1 if any_flag else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
