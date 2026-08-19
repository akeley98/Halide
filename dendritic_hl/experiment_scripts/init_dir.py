#!/usr/bin/env python3
"""Initialize one experiment directory for the LLM Halide-scheduling ablation.

    init_dir.py {data_dir} {label}

*label* is one of the four ablation cells -- harness_{T,F}_guide_{T,F} -- crossing
"agent has the dh_hl harness" with "agent has the scheduling guide".  This creates
`{data_dir}/{label}_{n}` (lowest n that avoids a collision) holding a symlink to
the fixed inputs, the guide contents (harness_F_guide_T), and a generated
`begin_experiment.py`; running that script later stands up the catalog.

Prints to stdout ONLY the non-symlink path to the experiment dir created.

Typo protection -- both flags are probed on the on-PATH dh_hl (the one the agent
runs) BEFORE any directory is made, so a mislabelled or stale setup fails loudly:
  * `{data_dir}` must already be a directory (so a mistyped path fails loudly
    rather than creating a stray tree).
  * Guide axis: `dh_hl help detail` exits 0 iff the guide is enabled, asserted
    `== label.endswith("guide_T")` (probed with the harness forced on, since
    `help` is itself a no-harness-blocklisted tool).
  * Harness axis: a blocklisted tool (`dh_hl status -h`) is available iff the
    harness is allowed, asserted `== label.startswith("harness_T")`.  The human
    sets DENDRITIC_HL_ALLOW_HARNESS / DENDRITIC_HL_GUIDE_ENABLED (or the flag
    defaults) to match the label before running this.

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

from datetime import datetime, timezone

def now_timestamp():
    """Current UTC wall-clock time as a dendritic_hl timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S_%fZ")


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
_GENERATOR_SRC = os.path.join(_HERE, "local_laplacian_experiment_generator.cpp")
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
_README_TEMPLATE = """\
# LLM Halide Scheduling Experiment {label}

Please run `./begin_experiment.py` and read and execute the resulting `prompt.md`.

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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", help="existing directory to create the "
                        "experiment subdirectory inside")
    parser.add_argument("label", choices=LABELS, help="experiment cell label")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.data_dir):
        parser.error("data_dir is not a directory: {!r} (typo protection)"
                     .format(args.data_dir))

    # Guide AND harness state must match the label BEFORE we create anything --
    # both probe the on-PATH dh_hl the agent will actually run, so a mis-set flag
    # (or a stale snapshot) fails loudly rather than silently mislabelling a run.
    expected_guide = label_enables_guide(args.label)
    actual_guide = _guide_enabled_via_cli()
    assert actual_guide == expected_guide, (
        "guide state mismatch: label {!r} expects guide {}, but `dh_hl help "
        "detail` reports guide {}. Enable/disable the guide in dh_hl to match "
        "the label, then re-run (no directory was created).".format(
            args.label, "enabled" if expected_guide else "disabled",
            "enabled" if actual_guide else "disabled"))

    expected_harness = label_allows_harness(args.label)
    actual_harness = _harness_allowed_via_cli()
    assert actual_harness == expected_harness, (
        "harness state mismatch: label {!r} expects the harness {}, but a "
        "blocklisted dh_hl tool is {}. Set DENDRITIC_HL_ALLOW_HARNESS (or the "
        "allow_harness_flag default in the on-PATH dh_hl) to match the label, "
        "then re-run (no directory was created).".format(
            args.label,
            "enabled" if expected_harness else "disabled (allowlist only)",
            "available" if actual_harness else "turned off"))

    exp_dir_name = _lowest_free_dir_name(args.data_dir, now_timestamp())
    exp_dir = os.path.join(args.data_dir, exp_dir_name)
    link_name = _lowest_free_dir_name(args.data_dir, args.label)
    os.makedirs(exp_dir)
    os.symlink(exp_dir_name, os.path.join(args.data_dir, link_name))

    shutil.copyfile(_GENERATOR_SRC,
                    os.path.join(exp_dir, "original_generator.cpp"))
    with open(os.path.join(exp_dir, "original_generator_parameters.json"),
              "w", encoding="utf-8") as f:
        f.write(_PARAMS_JSON)
    with open(os.path.join(exp_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(_README_TEMPLATE.format(label=args.label))

    # Guide contents shipped as plain files (moved here from begin_experiment.py
    # so that generated script never mentions the guide / --guide-only).
    if args.label == "harness_F_guide_T":
        _write_guide_contents(exp_dir)

    begin_path = os.path.join(exp_dir, "begin_experiment.py")
    with open(begin_path, "w", encoding="utf-8") as f:
        f.write(_render_begin_experiment(args.label))
    os.chmod(begin_path, 0o755)

    _unzip_halide(exp_dir)

    print(exp_dir)
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
    it later, itself via repr (write_runner)."""

    harness = label_allows_harness(label)
    guide = label_enables_guide(label)

    subs = {
        "@@LABEL@@": repr(label),
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
# Prompt
# --------------------------------------------------------------------------

def _make_prompt(harness, guide):
    """Prompt text, parameterized on (harness, guide)."""
    chunks = []

    maybe_min_time_req = ""
    maybe_perf_req = ""
    maybe_sub_agent_req = ""

    # Customize later.
    if True:
        maybe_min_time_req = "\nDo not stop until a minimum of one hour of effort."

    chunks.append(f"""\
You are the "main agent" participating in a controlled experiment
on fully autonomous LLM optimization of a Halide schedule.
You will optimize the Local Laplacian filter provided in `original_generator.cpp`.

Please work independently and try to make as much
progress as possible before stopping.
Note, the experiment must end within two hours.{maybe_min_time_req}

Local minima are everywhere! If performance plateaus,
consider if there's complete alternative strategies to try out.

Delegate tasks to sub-agents at your at your discretion,
as appropriate to support the goal of maximizing progress
within the bounds of one session.{maybe_sub_agent_req}
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
the whole generator is in one C++ file. The build tool logs all
programs as progress for the experiment.""")

    chunks.append(f"""

=== RULES ===

* The goal is to minimize the runtime for the specific problem size used by
  {'the harness' if harness else 'runner.py'}. \
Overfitting, including making assumptions that would
  break the pipeline on other problem sizes, is explicitly allowed.
  The scoring is the min of all schedules ever logged, not just the last.

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
  `./Halide/src/runtime/profiler_common.cpp` (custom profiler for experiment).

* Do not use the autoscheduler or try to read existing Local Laplacian apps.
  The generated Halide schedule must be your original work.
  (Caveat: we ignore the fact the "answer key" is likely in your training data).

* Use only Opus 4.8 for sub agents.

* Use `time.py` to get time elapsed.

""")
    return "\n".join(chunks)


if __name__ == "__main__":
    sys.exit(main())
