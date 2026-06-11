#!/usr/bin/env python3
"""
PreToolUse logging hook for the loopdoc micro-agent.

This is OBSERVATIONAL ONLY. It appends one JSON line per tool call to
loopdoc/tool_audit.jsonl and then ALWAYS exits 0 -- it never blocks a tool
call. Enforcement of the micro-agent's rules (no reading Halide source, no
commits, etc.) is left to after-the-fact review (see review_micro.py) plus the
human watching, per the project's "trust + audit" posture.

Wiring: referenced from the `hooks:` block of the micro-halide agent definition
(.claude/agents/micro-halide.md), so it fires only while that agent runs:

    command: python3 /ABS/PATH/loopdoc/log_tool_use.py micro-halide

The optional argv[1] is a label recorded as the agent name, so labeling is
correct even if the hook payload happens not to carry `agent_type`.

The hook receives a JSON object on stdin (Claude Code PreToolUse payload). We
read it defensively: any malformed/missing input is swallowed and we still exit
0, because a logging hook must never disrupt the agent it observes.
"""

import json
import os
import sys
import time

# Log next to THIS script (i.e. in loopdoc/), independent of cwd or
# CLAUDE_PROJECT_DIR, so the path is unambiguous wherever Claude is launched.
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_audit.jsonl")


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    tin = payload.get("tool_input") or {}
    # The single most informative argument, by tool:
    arg = (
        tin.get("file_path")        # Read / Edit / Write
        or tin.get("command")       # Bash
        or tin.get("pattern")       # Grep / Glob
        or tin.get("path")          # misc
        or tin.get("url")           # WebFetch (should never happen for micro)
    )

    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "agent": label or payload.get("agent_type") or "main",
        "agent_id": payload.get("agent_id", "-"),
        "session": payload.get("session_id", "-"),
        "tool": payload.get("tool_name", "?"),
        "arg": arg if isinstance(arg, str) else None,
    }

    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass  # Never let a logging failure break the agent.

    return 0


if __name__ == "__main__":
    sys.exit(main())
