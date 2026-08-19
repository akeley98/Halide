#!/usr/bin/env python3
"""
Usage: run_headless_experiment.py {data_dir} {label}

*label* is one of the four ablation cells -- harness_{T,F}_guide_{T,F} -- crossing
"agent has the dh_hl harness" with "agent has the scheduling guide".

"""


import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

# Set by the SIGUSR1 handler installed in run_experiment_streaming. The launcher
# (or an end_experiment.py the agent runs) does `kill -USR1 <launcher_pid>`; the
# handler sets this global and the runner's wait loop closes the input pipe -> a
# clean shutdown. Global by design (signal handlers can't take extra args).
_STOP = threading.Event()


def _on_stop_signal(signum, frame):
    _STOP.set()


# --------------------------------------------------------------------------
# 1. Pre-trust a directory (must run in the OUTER launcher, BEFORE `claude`).
# --------------------------------------------------------------------------
def trust_directory(exp_dir, config_dir=None):
    """Pre-accept Claude Code's folder-trust dialog for `exp_dir`, so a headless
    `claude -p` started in it does not block. Merge-writes
    projects["<abs path>"].hasTrustDialogAccepted = true into ~/.claude.json
    (or $CLAUDE_CONFIG_DIR/.claude.json), atomically, preserving all other state.

    Key `exp_dir` by the SAME path Claude will use: the git repo root, or the
    plain dir if it is not inside a git repo. Do NOT nest experiment dirs inside
    another git repo, or trust (and state siloing) resolves to the parent root.
    Run this while no `claude` process is writing that config for this run.
    """
    path = os.path.realpath(exp_dir)
    base = config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~")
    cfg = os.path.join(base, ".claude.json")
    data = json.load(open(cfg)) if os.path.exists(cfg) else {}
    data.setdefault("projects", {}).setdefault(path, {})["hasTrustDialogAccepted"] = True
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cfg) or ".", prefix=".claude.json.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, cfg)  # atomic
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path


# NOTE: single-shot build_claude_cmd + run_experiment moved to
# single_shot_legacy.py (QUICK-TEST ONLY; they kill background tasks). This
# module keeps exactly one runner: run_experiment_streaming.

# --------------------------------------------------------------------------
# 2. Shared helpers + token logging.
# --------------------------------------------------------------------------
def _usage(ev):
    u = (ev.get("message") or {}).get("usage") or {}
    return (int(u.get("input_tokens", 0) or 0),
            int(u.get("output_tokens", 0) or 0),
            int(u.get("cache_creation_input_tokens", 0) or 0),
            int(u.get("cache_read_input_tokens", 0) or 0))


def _text_of(content):
    """Flatten a tool_result/message `content` (str, or list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    out.append(b.get("text", ""))
                elif "content" in b:
                    out.append(_text_of(b["content"]))
        return " ".join(t for t in out if t)
    return ""


def pretty_lines(ev, width=200):
    """Best-effort human view of one stream-json event: assistant prose, each
    tool call, tool RESULTS (errors flagged), and system/result events. Returns a
    list of short strings (may be empty). The raw tee is the source of truth if a
    shape is missed here."""
    t = ev.get("type")
    lines = []
    if t == "assistant":
        for b in (ev.get("message") or {}).get("content") or []:
            if b.get("type") == "text" and (b.get("text") or "").strip():
                lines.append("[assistant] " + b["text"].strip().replace("\n", " ")[:width])
            elif b.get("type") == "tool_use":
                lines.append(f"[tool] {b.get('name')} {json.dumps(b.get('input', {}))[:width]}")
    elif t == "user":
        for b in (ev.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tag = "tool-error" if b.get("is_error") else "tool-result"
                lines.append(f"[{tag}] " + _text_of(b.get("content")).replace("\n", " ")[:width])
    elif t == "result":
        lines.append(f"[result] cost=${ev.get('total_cost_usd')} usage={ev.get('usage')}")
    elif t == "system" and ev.get("subtype") != "init":
        lines.append(f"[system:{ev.get('subtype')}] "
                     + json.dumps({k: v for k, v in ev.items()
                                   if k not in ("type", "subtype")})[:width])
    return lines


def env_with_paths(*dirs, base=None):
    """Copy of the environment with `dirs` prepended to PATH. Use this to make
    tools installed under e.g. ~/.local/bin resolvable inside `claude -p`, which
    runs non-interactively and does NOT source your shell profile. The agent's
    Bash tool inherits this env, so the prepended dirs become findable.
        env_with_paths(os.path.expanduser('~/.local/bin'))
    """
    env = dict(base if base is not None else os.environ)
    if dirs:
        env["PATH"] = os.pathsep.join(
            [os.path.abspath(os.path.expanduser(d)) for d in dirs] + [env.get("PATH", "")])
    return env


# --------------------------------------------------------------------------
# 3. PERSISTENT streaming-input session (background tasks + async resume WORK).
# --------------------------------------------------------------------------
# Single-shot `claude -p "<prompt>"` exits when the first turn ends, which kills
# run_in_background tasks and prevents async notify-and-resume. Streaming-input
# mode (--input-format stream-json, stdin held open) keeps ONE session alive
# across turns: background tasks survive, and the harness auto-delivers a
# task_notification when they finish, waking the model to continue. This is what
# enables sub-agent parallelism / background work in headless runs.
#
# The session no longer self-terminates, so the launcher decides when to stop.
def run_experiment_streaming(exp_dir, initial_prompt, token_log, raw_log=None,
                             monitor=True, monitor_stream=None, env=None,
                             model="claude-opus-4-8", permission_mode="auto",
                             effort=None, allowed_tools=None, extra_args=None,
                             session_id=None,
                             done_file=None, stop_event=None,
                             stop_signal=signal.SIGUSR1, reminder_period=600,
                             max_seconds=7200, idle_seconds=None):
    # Close stdin (-> clean shutdown) on the FIRST of:
    #   stop_signal : SIGUSR1 by default. `kill -USR1 <launcher_pid>` (e.g. from an
    #                 end_experiment.py the agent runs) trips a global; the wait
    #                 loop then closes the input pipe. Installed only on main thread.
    #   done_file   : path the agent `touch`es when finished (signal-free alt).
    #   stop_event  : threading.Event the launcher sets itself.
    #   max_seconds : hard wall-clock watchdog (default 2h).
    #   idle_seconds: no stdout for this long (hang guard; don't rely on it if a
    #                 quiet-but-live background task could still be running).
    # reminder_period: every N seconds, inject a stdin nudge with time remaining +
    #   "run end_experiment.py if you have no more tasks". None disables. Each
    #   nudge is a turn, so keep the period generous.
    import sys
    mon = monitor_stream or sys.stderr
    session_id = session_id or str(uuid.uuid4())   # known up front; resume with it later
    # Install the SIGUSR1 -> _STOP handler (signal handlers must be set on the
    # main thread; if we're not on it, skip and rely on done_file/stop_event).
    _prev_handler = None
    if stop_signal is not None and threading.current_thread() is threading.main_thread():
        _STOP.clear()
        _prev_handler = signal.signal(stop_signal, _on_stop_signal)
    else:
        stop_signal = None
    cmd = ["claude", "-p",
           "--input-format", "stream-json",
           "--output-format", "stream-json", "--verbose",
           "--model", model, "--permission-mode", permission_mode,
           "--session-id", session_id]
    if effort:
        cmd += ["--effort", effort]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    cmd += list(extra_args or [])

    proc = subprocess.Popen(cmd, cwd=exp_dir, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, bufsize=1, env=env)

    state = {"ci": 0, "co": 0, "cc": 0, "cr": 0, "last": time.time(),
             "final_usage": {}, "cost": None}
    logf = open(token_log, "w")
    raw = open(raw_log, "w") if raw_log else None

    def reader():
        for line in proc.stdout:
            state["last"] = time.time()
            if raw:
                raw.write(line); raw.flush()
            s = line.strip()
            if not s:
                continue
            try:
                ev = json.loads(s)
            except json.JSONDecodeError:
                continue
            if monitor:
                for pl in pretty_lines(ev):
                    mon.write(pl + "\n"); mon.flush()
            et = ev.get("type")
            if et == "assistant":
                i, o, ka, kr = _usage(ev)
                if i or o or ka or kr:
                    state["ci"] += i; state["co"] += o
                    state["cc"] += ka; state["cr"] += kr
                    logf.write(json.dumps({
                        "utc": datetime.now(timezone.utc).isoformat(),
                        "role": "subagent" if ev.get("parent_tool_use_id") else "main",
                        "input": i, "output": o,
                        "cache_creation": ka, "cache_read": kr,
                        "cum_input": state["ci"], "cum_output": state["co"],
                        "cum_cache_creation": state["cc"], "cum_cache_read": state["cr"],
                    }) + "\n"); logf.flush()
            elif et == "result":
                state["final_usage"] = ev.get("usage") or state["final_usage"]
                if ev.get("total_cost_usd") is not None:
                    state["cost"] = ev.get("total_cost_usd")

    th = threading.Thread(target=reader, daemon=True); th.start()

    # Send the initial prompt as one JSON user message, then hold stdin open.
    proc.stdin.write(json.dumps(
        {"type": "user", "message": {"role": "user", "content": initial_prompt}}) + "\n")
    proc.stdin.flush()

    start = time.time()
    last_reminder = start
    try:
        while proc.poll() is None:
            time.sleep(1.0)
            now = time.time()
            if _STOP.is_set():                                  # SIGUSR1 arrived
                break
            if done_file and os.path.exists(done_file):
                break
            if stop_event is not None and stop_event.is_set():
                break
            if max_seconds and now - start > max_seconds:
                break
            if idle_seconds and now - state["last"] > idle_seconds:
                break
            if reminder_period and now - last_reminder >= reminder_period:
                remaining = int(max_seconds - (now - start)) if max_seconds else None
                note = ((f"{remaining} seconds remaining. " if remaining is not None else "")
                        + "Reminder: if you have no more tasks to do, run "
                          "`python3 end_experiment.py` to end the experiment.")
                try:
                    proc.stdin.write(json.dumps(
                        {"type": "user", "message": {"role": "user", "content": note}}) + "\n")
                    proc.stdin.flush()
                except (BrokenPipeError, ValueError, OSError):
                    break
                last_reminder = now
    finally:
        try:
            proc.stdin.close()          # closing stdin ends the session
        except Exception:
            pass
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        th.join(timeout=5)
        logf.close()
        if raw:
            raw.close()
        if stop_signal is not None and _prev_handler is not None:
            try:
                signal.signal(stop_signal, _prev_handler)   # restore prior handler
            except Exception:
                pass
    return {"session_id": session_id, "final_usage": state["final_usage"],
            "total_cost_usd": state["cost"], "wall_seconds": round(time.time() - start, 1)}
