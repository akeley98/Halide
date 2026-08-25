#!/usr/bin/env python3
"""Install dh_hl, init one experiment directory, start the LLM Halide scheduling.

    run_headless.py {data_dir} {label}

*label* is one of the four ablation cells -- harness_{T,F}_guide_{T,F} -- crossing
"agent has the dh_hl harness" with "agent has the scheduling guide".  This creates
`{data_dir}/{_blindfold_label(label)}_{n}` (lowest n that avoids a collision) holding
the fixed inputs, the guide contents (harness_F_guide_T), and a generated
`begin_experiment.py`; running that script later stands up the catalog.

If no --dir-only arg is passed, after the directory is set up and dh_hl installed,
`claude -p` is launched to run the experiment, and the post-experiment profiling is run.

NOTE: for the no-harness cells `begin_experiment.py` ships `runner.py` (run a
RunGenMain binary with the standard benchmark args) and `build.py`
(build Halide generator and binaries by recycling minimal dh_hl internals;
the rest of dh_hl is off limits using tool blocklisting).
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone


def now_timestamp():
    """Current UTC wall-clock time as a dendritic_hl timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S_%fZ")


initial_prompt = "Hello! You are participating in a study on autonomous LLM-guided optimization of Halide programs. When the experiment begins, a new git repo will be initialized for you, which you may use without human oversight. Please run `begin_experiment.py` to start, and read and execute the resulting `prompt.md`."

DEFAULT_MAX_SECONDS = 7200 + 60  # Give undocumented 1 minute grace period.

# App name
# Not magic; you have to make all the generator files etc. yourself.
APP = "tile_match"

_HERE = os.path.dirname(os.path.abspath(__file__))
# experiment_scripts/ lives directly under the harness source dir (dendritic_hl/).
_REPO_DIR = os.path.dirname(_HERE)
# Invoke `dh_hl` off PATH -- the stable launcher install_snapshot.sh drops at
# ~/.local/bin/dh_hl -- NOT this dev tree's ./dh_hl.  That is the exact frozen
# harness the agent is prompted to run, so the guide check here and every dh_hl
# call in the generated begin_experiment.py exercise the same binary the agent
# does.
_DH_HL = "dh_hl"
_DETAIL_DIR = os.path.join(_REPO_DIR, "detail")
_EXAMPLES_DIR = os.path.join(_REPO_DIR, "examples")

if APP == "local_laplacian":
    template_path = _HERE
    # RunGenMain arguments giving the problem sizes that used to be the generator's
    # set_estimates (input/output 1536x2560x3; levels=8, alpha=1, beta=1).
    # --estimate_all no longer works now that the estimates are stripped from the
    # generator, so the sizes are explicit.  Verified end-to-end against ~/Halide:
    # `dh_hl build --profile` compiles this generator and benchmarks it under this
    # problem, with output throughput matching a 1536x2560 frame.
    _PROBLEM_ARGV = [
        "<RunGenMain>", "--benchmarks=all",
        "--output_extents=[1536,2560,3]",
        "input=random:0:[1536,2560,3]", "levels=8", "alpha=1", "beta=1",
    ]
elif APP == "tile_match":
    # ========================================================================
    # CRITICAL rule with REAL WORLD CONSEQUENCES:
    # The tile_match pipeline is sourced from Adobe proprietary software.
    # It MUST be stored outside this Halide repository to prevent that we
    # ever accidentally push it to the open source Halide repository.
    # ========================================================================
    template_path = os.path.join(_HERE, "../../../proprietary_tile_match/")
    _PROBLEM_ARGV = [
        "<RunGenMain>", "--benchmarks=all",
        "--output_extents=[200,300,3]",
        "img_ref=random:1:[1536,2560]",
        "img_alt=random:2:[1536,2560]",
        "match_uv_low=random:3:[100,150,2]",
        "up_scale=2", "tile_size_low=16", "search_radius=4", "black_level=0", "white_level=1023",
        "base_sum_intensity=random:4:[200,300,1]"
    ]
else:
    raise ValueError(f"TODO implement {APP}")

no_schedule_generator_path = os.path.join(template_path, f"{APP}_experiment_generator.cpp")
answer_key_parameters_path = os.path.join(template_path, f"{APP}_parameters.json")
answer_key_generator_path = os.path.join(template_path, f"{APP}_answer_key.cpp")

# The second-level script is kept in its own .py file (discovered by relative
# path) so an editor highlights it as Python, not one giant string literal.
_TEMPLATE_PATH = os.path.join(_HERE, "begin_experiment_template.py")
_HALIDE_TGZ_PATH = os.path.join(_HERE, "Halide.tgz")

LABELS = ("harness_T_guide_T", "harness_T_guide_F",
          "harness_F_guide_T", "harness_F_guide_F")

# original_generator_parameters.json: a length-1 list with one empty object
# ("benchmark once, no generator parameters"), matching new_catalog's default.
_PARAMS_JSON = "[\n  {\n  }\n]\n"

# The two-phase hidden prompt here is because begin_experiment.py logs
# the start time of the experiment in the catalog, and I want that to
# happen when the agent actually starts, not when init_dir runs.
_README = """\
# LLM Halide Scheduling Experiment

Please run `./begin_experiment.py` and read and execute the resulting `prompt.md`.

"""

_END_EXPERIMENT_TEMPLATE = """#!/usr/bin/env python3
import os
import signal
os.kill(@@PID@@, signal.SIGUSR1)
"""


def _dh_env(allow_harness):
    """os.environ with DENDRITIC_HL_ALLOW_HARNESS forced on/off (None = inherit)."""
    env = dict(os.environ)
    if allow_harness is not None:
        env["DENDRITIC_HL_ALLOW_HARNESS"] = "1" if allow_harness else "0"
    return env


def _guide_enabled_via_cli():
    """True iff `dh_hl help detail` exits 0 (i.e. the guide is enabled).  Run with
    the harness forced ON: `help` is a blocklisted tool, so a no-harness ambient
    DENDRITIC_HL_ALLOW_HARNESS would otherwise block the probe itself and mask the
    guide state we are trying to read (the two flags are independent)."""
    r = subprocess.run([_DH_HL, "help", "detail"], env=_dh_env(allow_harness=True),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def _harness_allowed_via_cli():
    """True iff a blocklisted tool is available under the AMBIENT allow-harness
    setting (no override): `dh_hl status -h` exits 0 when the harness is on, and is
    turned off (nonzero) when it is not.  Checks that the environment the agent
    will run in matches the label's harness axis."""
    r = subprocess.run([_DH_HL, "status", "-h"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def _unzip_halide(exp_dir):
    r = subprocess.run(["tar", "-xzf", _HALIDE_TGZ_PATH, "-C", exp_dir])
    if r.returncode != 0:
        raise ValueError("Extract Halide.tgz failed")
    halide_h_path = os.path.join(exp_dir, "Halide/build/include/Halide.h")
    r = subprocess.run(["ls", halide_h_path], stdout=subprocess.DEVNULL)
    if r.returncode != 0:
        raise ValueError(f"Extracted Halide.tgz missing {halide_h_path}")


def _write_guide_contents(exp_dir):
    """harness_F_guide_T only: the agent has no harness to serve the guide, so ship
    it as plain files next to the prompt.  `dh_hl prompt --guide-only` is a
    blocklisted tool, so run it with the harness forced ON -- this script is
    trusted setup, not the agent."""
    guide = subprocess.run(
        [_DH_HL, "prompt", "--guide-only"], env=_dh_env(allow_harness=True),
        check=True, capture_output=True, text=True).stdout
    with open(os.path.join(exp_dir, "guide.md"), "w", encoding="utf-8") as f:
        f.write(guide)
    shutil.copytree(_DETAIL_DIR, os.path.join(exp_dir, "detail"))
    shutil.copytree(_EXAMPLES_DIR, os.path.join(exp_dir, "examples"))


def _lowest_free_dir_name(data_dir, label):
    n = 0
    while True:
        candidate = "{}_{}".format(label, n)
        if not os.path.exists(os.path.join(data_dir, candidate)):
            return candidate
        n += 1


def label_allows_harness(label):
    return label.startswith("harness_T")


def label_enables_guide(label):
    return label.endswith("guide_T")


def _blindfold_label(label):
    return f"{int(label_allows_harness(label))}{int(label_enables_guide(label))}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", help="existing directory to create the "
                        "experiment subdirectory inside")
    parser.add_argument("label", choices=LABELS, help="experiment cell label")
    parser.add_argument("--dir-only", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--begin-end", action=argparse.BooleanOptionalAction, default=False)
    args = parser.parse_args(argv)
    dir_only = args.dir_only
    begin_end = args.begin_end

    if begin_end and not dir_only:
        parser.error("--begin-end requires --dir-only (otherwise agent will be confused)")

    if not os.path.isdir(args.data_dir):
        parser.error("data_dir is not a directory: {!r} (typo protection)"
                     .format(args.data_dir))

    # Install dh_hl snapshot with correct guide/harness.
    expected_harness = label_allows_harness(args.label)
    expected_guide = label_enables_guide(args.label)
    subprocess.run([
            os.path.join(_REPO_DIR, "install_snapshot.sh"),
            str(int(expected_harness)),
            str(int(expected_guide))],
        check=True,
    )

    # Guide AND harness state must match the label BEFORE we create anything --
    # both probe the on-PATH dh_hl the agent will actually run, so a mis-set flag
    # (or a stale snapshot) fails loudly rather than silently mislabelling a run.
    actual_guide = _guide_enabled_via_cli()
    assert actual_guide == expected_guide, (
        "guide state mismatch: label {!r} expects guide {}, but `dh_hl help "
        "detail` reports guide {}. Enable/disable the guide in dh_hl to match "
        "the label, then re-run (no directory was created).".format(
            args.label, "enabled" if expected_guide else "disabled",
            "enabled" if actual_guide else "disabled"))

    actual_harness = _harness_allowed_via_cli()
    assert actual_harness == expected_harness, (
        "harness state mismatch: label {!r} expects the harness {}, but a "
        "blocklisted dh_hl tool is {}. Set DENDRITIC_HL_ALLOW_HARNESS (or the "
        "allow_harness_flag default in the on-PATH dh_hl) to match the label, "
        "then re-run (no directory was created).".format(
            args.label,
            "enabled" if expected_harness else "disabled (allowlist only)",
            "available" if actual_harness else "turned off"))

    exp_dir_name = _lowest_free_dir_name(args.data_dir, _blindfold_label(args.label))
    exp_dir = os.path.join(args.data_dir, exp_dir_name)
    os.makedirs(exp_dir)

    print(f"Experiment dir: {exp_dir}", file=sys.stderr)

    shutil.copyfile(no_schedule_generator_path,
                    os.path.join(exp_dir, "original_generator.cpp"))
    with open(os.path.join(exp_dir, "original_generator_parameters.json"),
              "w", encoding="utf-8") as f:
        f.write(_PARAMS_JSON)
    with open(os.path.join(exp_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(_README)

    # Guide contents shipped as plain files (moved here from begin_experiment.py
    # so that generated script never mentions the guide / --guide-only).
    if args.label == "harness_F_guide_T":
        _write_guide_contents(exp_dir)

    # Write begin_experiment.py
    begin_path = os.path.join(exp_dir, "begin_experiment.py")
    with open(begin_path, "w", encoding="utf-8") as f:
        f.write(_render_begin_experiment(args.label))
    os.chmod(begin_path, 0o755)

    # Write end_experiment.py
    end_path = os.path.join(exp_dir, "end_experiment.py")
    with open(end_path, "w", encoding="utf-8") as f:
        f.write(_END_EXPERIMENT_TEMPLATE.replace("@@PID@@", str(os.getpid())))
    os.chmod(end_path, 0o755)

    # Extract ./Halide
    _unzip_halide(exp_dir)

    # Make experiment/ sub-directory and save UUID
    os.mkdir(os.path.join(exp_dir, "experiment"))
    session_id = str(uuid.uuid4())
    with open(os.path.join(exp_dir, "experiment/session_id.txt"), "w", encoding="utf-8") as f:
        f.write(session_id)
        f.write("\n")

    # Trust experiment directory in Claude
    if not dir_only:
        trust_directory(exp_dir)

    # Run Claude (may be skipped inside due to dir_only)
    run_experiment_streaming(
        exp_dir=exp_dir,
        initial_prompt=initial_prompt,
        token_log=os.path.join(exp_dir, "experiment/token_log.jsonl"),
        raw_log=os.path.join(exp_dir, "experiment/raw_log.jsonl"),
        effort="xhigh",
        session_id=session_id,
        dir_only=args.dir_only,
    )

    if begin_end:
        subprocess.run(["python3", os.path.join(exp_dir, "begin_experiment.py")])
        subprocess.run(["python3", os.path.join(exp_dir, "end_experiment.py")])

    # Run profiler
    prof_cmd = [
        "python3",
        os.path.join(_HERE, "profiler_session.py"),
        os.path.join(exp_dir, "catalog.dh_hl"),
        answer_key_generator_path,
        answer_key_parameters_path,
        "--json-append",
        os.path.join(args.data_dir, "sessions.json"),
    ]
    print(prof_cmd, file=sys.stderr)
    if not dir_only:
        subprocess.run(prof_cmd, check=False)

    return 0


def _render_begin_experiment(label):
    """Return the text of the `begin_experiment.py` to drop in the new dir, read
    from `begin_experiment_template.py` with this experiment's fixed values baked
    in (absolute paths, so the generated script is location-independent).

    Each value is inserted as its `repr()` -- the template's sentinels are bare
    (e.g. `DH_HL = @@DH_HL@@`), so repr supplies the quotes AND the escaping.
    That keeps a path with awkward characters (a Windows backslash, a quote, a
    space) from corrupting the generated source.  The template's @@RUN_ARGS@@
    sentinel is intentionally left untouched -- the generated script substitutes
    it later, itself via repr (write_runner).

    Docstring for begin experiment file (moved here to avoid showing in
    rendered begin_experiment.py):

    Stands up the experiment: writes prompt.md, (for harness_F_guide_T) the guide
    contents, (for the no-harness cells) runner.py, and finally the dh_hl catalog +
    sized problem + `experiment begin`.

    This file is the TEMPLATE that init_dir.py copies out, replacing the bare @@...@@
    sentinels (LABEL / DH_HL / DETAIL_DIR / EXAMPLES_DIR) with repr()'d values -- so
    the substituted paths are always correctly quoted and escaped (a Windows path's
    backslashes included).  The sentinels are bare (NOT inside quotes), so this
    template does not parse as Python on its own; that is intentional -- you cannot
    run it directly, and letting repr supply the quoting is what makes the escaping
    safe.  @@RUN_ARGS@@ is left for the *generated* script to substitute at runtime
    (see write_runner).
"""

    harness = label_allows_harness(label)
    guide = label_enables_guide(label)

    subs = {
        "@@LABEL@@": repr(label),  # I wanted to not leak this to agents but hard to fix.
        "@@NEED_BUILD_PY@@": repr(not harness),
        "@@PROBLEM_ARGV@@": repr(_PROBLEM_ARGV),
        "@@DH_HL@@": repr(_DH_HL),
        "@@DETAIL_DIR@@": repr(_DETAIL_DIR),
        "@@EXAMPLES_DIR@@": repr(_EXAMPLES_DIR),
        "@@PROMPT@@": repr(_make_prompt(harness, guide)),
    }
    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    for key, value in subs.items():
        text = text.replace(key, value)
    return text


# --------------------------------------------------------------------------
# Prompt File
# --------------------------------------------------------------------------

def _make_prompt(harness, guide):
    """Prompt text, parameterized on (harness, guide)."""
    chunks = []

    chunks.append(f"""\
You are the "main agent" participating in a controlled experiment
on fully autonomous LLM optimization of a Halide schedule.
You will optimize the {APP} filter provided in `original_generator.cpp`,
using only the CPU (ignore the GPU, if any, on this system).

Please work independently and try to make as much
progress as possible before stopping.
Local minima are everywhere! If performance plateaus,
consider if there's complete alternative strategies to try out.

Delegate tasks to sub-agents at your discretion,
as appropriate to support the goal of maximizing progress
within the bounds of one session.
""")

    if harness:
        chunks.append("""\
Run `dh_hl prompt --main` and read the result IN FULL
for the usage instructions of the `dh_hl` experiment harness,
which builds and logs the history of Halide code for the experiment.
This reading is critical, as testing the efficacy of the provided
information is the independent variable for the experiment.""")

    if not harness and guide:
        chunks.append("""\
Read `guide.md` IN FULL prior to starting the experiment.
This reading is critical, as testing the efficacy of the provided
information is the independent variable for the experiment.""")

    if not guide:
        chunks.append("""\
For this experiment, you will rely on your own knowledge of Halide,
from prior training, Halide experimentation, or reading Halide code/docs.""")

    if guide:
        chunks.append(f"""\
The {'prompt' if harness else 'guide'} mentions supplemental reading.
These are entirely optional.
You may also do your own Halide experimentation or read Halide code/docs.""")

    chunks.append("The Halide header for this experiment is in `./Halide/build/include/Halide.h`.")
    if harness:
        chunks.append("The Halide path is `./Halide/`.")
    chunks.append("The built Halide programs enable a custom profiler for the experiment.")
    chunks.append("This prints top-line stats (pipeline stats) and a table of per-func stats.")
    if not harness:
        chunks.append("The environment variable `HL_PROFILER_JSON_OUTPUT`")
        chunks.append("names a file to dump JSON top-line and per-func stats to.")
        chunks.append("The environment variable `HL_PROFILER_JSON_TEMPORARY_WARNINGS`")
        chunks.append("names a file to dump JSON warnings to.")

    if not harness:
        chunks.append("")
        chunks.append("""\
Read the `build.py` script to understand the build tool.
DON'T inspect the underlying `dh_hl` tool (experiment infrastructure).
You may copy the build script and C++ source as you wish, as long as
the whole generator is in one C++ file. The build tool logs
a catalog of programs as progress for the experiment.
The underlying process group may be killed by SIGINT (^C) safely.
The tool is safely usable in parallel; the catalog does locking and rollbacks.

""")

    chunks.append(f"""

=== RULES ===

* The goal is to minimize the runtime for the specific problem size used by
  {'the harness' if harness else 'runner.py'}. \
Overfitting, including making assumptions that would
  break the pipeline on other problem sizes, is explicitly allowed.
  The scoring is based on benchmarking all schedules ever logged, not
  just the last. The score is the schedule with the lowest cost,
  based on a large number of benchmarks run strictly after the experiment.
  The `bound` function is safely allowed as part of this:
  incorrect `bound` usage for the problem size is detected in benchmarking,
  and these failed benchmarks are not scored.

* Modify only the Halide schedule, not the Halide algorithm
  (further instructions inside the provided generator C++ source).
  The code must remain functionally correct for the tested problem size.

* You may make git commits or git worktrees in this directory at any
  time without human oversight.
""")

    if harness:
        chunks.append("""\
* Only edit files inside this directory, EXCLUDING `experiment/`, `catalog.dh_hl`
  (interact with it only through `dh_hl`). These must remain git ignored.""")

    if not harness:
        chunks.append("""\
* Only edit files inside this directory, EXCLUDING `experiment/`, `catalog.dh_hl`
  (experiment private logging state). These must remain git ignored.""")

    chunks.append("""
* DON'T read any files except those in this directory;
  do not use web tools to seek outside information.
  However, you are *strongly* encouraged to read relevant files in `./Halide`
  if needed to clarify Halide usage. Consider in particular
  `./Halide/src/Func.h` (header for per-Func scheduling directives),
  `./Halide/src/runtime/profiler_common.cpp` (custom profiler for experiment).""")

    chunks.append(f"""
* Do not use the autoscheduler or try to read existing {APP} apps.
  The generated Halide schedule must be your original work.
  (Caveat: we ignore the fact the "answer key" is likely in your training data).""")

    chunks.append("""
* Use only Opus 4.8 with xhigh effort for sub agents.
  Comply to the best of your ability (e.g. if you can't control
  version number, plain Opus is acceptable).

* Use `time.py` to get time elapsed.

* The experiment must end within two hours.

* Work at least one hour before ending the experiment.
  Try different approaches if stuck in a local minimum
  and there is not yet one hour of effort spent.

* Unless under severe time pressure, before ending the experiment,
  output a file issues.md listing problems encountered regarding the
  experiment toolchain or custom profiler.
  PLEASE REMEMBER: if you `2>&1 | head` you will probably skip
  error messages, and you will get the (successful) return code of `head`.

* Use `end_experiment.py` when all your tasks are done.
  NOTE: this may kill any running sub-agents and background tasks!

NOTE: the build/add-schedule tool takes a global lock non-exclusively.
The profiler runner tool takes that same lock exclusively.
""")
    assert DEFAULT_MAX_SECONDS >= 7200, "update 2 hour warning"

    return "\n".join(chunks)


# Set by the SIGUSR1 handler installed in run_experiment_streaming. The launcher
# (or an end_experiment.py the agent runs) does `kill -USR1 <launcher_pid>`; the
# handler sets this global and the runner's wait loop closes the input pipe -> a
# clean shutdown. Global by design (signal handlers can't take extra args).
_STOP = threading.Event()


def _on_stop_signal(signum, frame):
    print(f"{os.getpid()} got signal {signum}", file=sys.stderr)
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
# dir_only=True causes the claude command to be printed to stderr instead of executed.
#
# The session no longer self-terminates, so the launcher decides when to stop.
def run_experiment_streaming(exp_dir, initial_prompt, token_log, raw_log=None,
                             monitor=True, monitor_stream=None, env=None,
                             model="claude-opus-4-8", permission_mode="auto",
                             effort=None, allowed_tools=None, extra_args=None,
                             session_id=None,
                             done_file=None, stop_event=None,
                             stop_signal=signal.SIGUSR1, reminder_period=600,
                             max_seconds=DEFAULT_MAX_SECONDS, idle_seconds=None,
                             dir_only=False):
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

    if dir_only:
        print(cmd, file=sys.stderr)
        return None

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


if __name__ == "__main__":
    sys.exit(main())
