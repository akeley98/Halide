#!/usr/bin/env python3
"""Initialize one experiment directory for the LLM Halide-scheduling ablation.

    init_dir.py {data_dir} {label}

*label* is one of the four ablation cells -- harness_{T,F}_guide_{T,F} -- crossing
"agent has the dh_hl harness" with "agent has the scheduling guide".  This creates
`{data_dir}/{label}_{n}` (lowest n that avoids a collision) holding the fixed
inputs plus a generated `begin_experiment.py`; running that script later stands up
the catalog and (for the relevant cells) the guide contents.

Typo protection:
  * `{data_dir}` must already be a directory (so a mistyped path fails loudly
    rather than creating a stray tree).
  * The label's guide state must match reality: `dh_hl help detail` exits 0 iff the
    guide is enabled, so we assert `(exit == 0) == label.endswith("guide_T")`.  The
    human flips the guide in `dh_hl` by hand before running this; the assert catches
    a label/flag mismatch BEFORE any directory is made.

NOTE: the "Build Helpers" (a `bin/` + `build.py` for the no-harness cells) are NOT
yet implemented -- `begin_experiment.py` only prints a deferred-TODO for them.
"""

import argparse
import os
import shutil
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
# experiment_scripts/ lives directly under the harness source dir (dendritic_hl/).
_REPO_DIR = os.path.dirname(_HERE)
_DH_HL = os.path.join(_REPO_DIR, "dh_hl")
_DETAIL_DIR = os.path.join(_REPO_DIR, "detail")
_EXAMPLES_DIR = os.path.join(_REPO_DIR, "examples")
_GENERATOR_SRC = os.path.join(_HERE, "local_laplacian_experiment_generator.cpp")

LABELS = ("harness_T_guide_T", "harness_T_guide_F",
          "harness_F_guide_T", "harness_F_guide_F")

# original_generator_parameters.json: a length-1 list with one empty object
# ("benchmark once, no generator parameters"), matching new_catalog's default.
_PARAMS_JSON = "[\n  {\n  }\n]\n"

_README_PLACEHOLDER = """\
# Experiment {label}

PLACEHOLDER README (the real contents are embedded later).

This directory was created by `experiment_scripts/init_dir.py`.  Run
`./begin_experiment.py` from inside it to stand up the catalog.
"""


def _guide_enabled_via_cli():
    """True iff `dh_hl help detail` exits 0 (i.e. the guide is enabled)."""
    import subprocess
    r = subprocess.run([_DH_HL, "help", "detail"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def _lowest_free_dir(data_dir, label):
    n = 0
    while True:
        candidate = os.path.join(data_dir, "{}_{}".format(label, n))
        if not os.path.exists(candidate):
            return candidate
        n += 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", help="existing directory to create the "
                        "experiment subdirectory inside")
    parser.add_argument("label", choices=LABELS, help="experiment cell label")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.data_dir):
        parser.error("data_dir is not a directory: {!r} (typo protection)"
                     .format(args.data_dir))

    # Guide state must match the label BEFORE we create anything.
    expected_guide = args.label.endswith("guide_T")
    actual_guide = _guide_enabled_via_cli()
    assert actual_guide == expected_guide, (
        "guide state mismatch: label {!r} expects guide {}, but `dh_hl help "
        "detail` reports guide {}. Enable/disable the guide in dh_hl to match "
        "the label, then re-run (no directory was created).".format(
            args.label, "enabled" if expected_guide else "disabled",
            "enabled" if actual_guide else "disabled"))

    exp_dir = _lowest_free_dir(args.data_dir, args.label)
    os.makedirs(exp_dir)

    shutil.copyfile(_GENERATOR_SRC,
                    os.path.join(exp_dir, "original_generator.cpp"))
    with open(os.path.join(exp_dir, "original_generator_parameters.json"),
              "w", encoding="utf-8") as f:
        f.write(_PARAMS_JSON)
    with open(os.path.join(exp_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(_README_PLACEHOLDER.format(label=args.label))

    begin_path = os.path.join(exp_dir, "begin_experiment.py")
    with open(begin_path, "w", encoding="utf-8") as f:
        f.write(_render_begin_experiment(args.label))
    os.chmod(begin_path, 0o755)

    print(exp_dir)
    return 0


def _render_begin_experiment(label):
    """Return the text of the `begin_experiment.py` to drop in the new dir, with
    this experiment's fixed values baked in (absolute paths, so the generated
    script is location-independent)."""
    subs = {
        "@@LABEL@@": label,
        "@@DH_HL@@": _DH_HL,
        "@@DETAIL_DIR@@": _DETAIL_DIR,
        "@@EXAMPLES_DIR@@": _EXAMPLES_DIR,
    }
    text = _BEGIN_EXPERIMENT_TEMPLATE
    for key, value in subs.items():
        # Values are absolute paths / a fixed label, so a plain replace is safe.
        text = text.replace(key, value)
    return text


# The generated second-level script.  It "doesn't have to be pretty" (per the
# task): fixed values are substituted in via @@...@@ sentinels above, and it may
# embed large literals directly.  Kept as a raw string so its own braces / %
# never collide with any formatting here.
_BEGIN_EXPERIMENT_TEMPLATE = r'''#!/usr/bin/env python3
"""Generated by init_dir.py -- run ONCE from inside this experiment directory.

Stands up the experiment: writes prompt.md, (for harness_F_guide_T) the guide
contents, and finally the dh_hl catalog + sized problem + `experiment begin`.
The "Build Helpers" for the no-harness cells are not implemented yet -- see
write_build_helpers().
"""

import os
import shutil
import subprocess
import sys

LABEL = "@@LABEL@@"
DH_HL = "@@DH_HL@@"
DETAIL_DIR = "@@DETAIL_DIR@@"
EXAMPLES_DIR = "@@EXAMPLES_DIR@@"

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = LABEL.startswith("harness_T")   # agent gets the dh_hl harness
GUIDE = LABEL.endswith("guide_T")         # agent gets the scheduling guide

GENERATOR = os.path.join(HERE, "original_generator.cpp")
PARAMS = os.path.join(HERE, "original_generator_parameters.json")
PROMPT_MD = os.path.join(HERE, "prompt.md")
CATALOG = os.path.join(HERE, "catalog.dh_hl")


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

def add_prompt():
    """PLACEHOLDER prompt text, parameterized on (HARNESS, GUIDE).

    The human fills this in.  Most text is shared across >=2 of the 4 cells, so
    build it from a common block plus per-cell additions.  The four branches
    below make it obvious where each cell's text goes."""
    common = "add prompt\n"

    if HARNESS and GUIDE:
        specific = ""       # harness_T_guide_T: harness + guide
    elif HARNESS and not GUIDE:
        specific = ""       # harness_T_guide_F: harness, no guide
    elif not HARNESS and GUIDE:
        specific = ""       # harness_F_guide_T: no harness, guide shipped as files
    else:
        specific = ""       # harness_F_guide_F: neither

    return common + specific


def write_prompt():
    with open(PROMPT_MD, "w", encoding="utf-8") as f:
        f.write(add_prompt())


# --------------------------------------------------------------------------
# Guide contents (harness_F_guide_T only): the agent has no harness to serve the
# guide, so ship it as plain files next to the prompt.
# --------------------------------------------------------------------------

def write_guide_contents():
    guide = subprocess.run([DH_HL, "prompt", "--guide-only"],
                           check=True, capture_output=True, text=True).stdout
    with open(os.path.join(HERE, "guide.md"), "w", encoding="utf-8") as f:
        f.write(guide)
    shutil.copytree(DETAIL_DIR, os.path.join(HERE, "detail"))
    shutil.copytree(EXAMPLES_DIR, os.path.join(HERE, "examples"))


# --------------------------------------------------------------------------
# Build helpers (no-harness cells) -- NOT YET IMPLEMENTED (deferred).
# --------------------------------------------------------------------------

def write_build_helpers():
    print("NOTE: build helpers (bin/ + build.py) are not implemented yet; "
          "the no-harness build environment is missing for " + LABEL,
          file=sys.stderr)


# --------------------------------------------------------------------------
# Catalog (always; the LAST step -- `experiment begin` logs the start time).
# --------------------------------------------------------------------------

# RunGenMain arguments giving the problem sizes that used to be the generator's
# set_estimates (input/output 1536x2560x3; levels=8, alpha=1, beta=1).
# --estimate_all no longer works now that the estimates are stripped from the
# generator, so the sizes are explicit.  Verified end-to-end against ~/Halide:
# `dh_hl build --profile` compiles this generator and benchmarks it under this
# problem, with output throughput matching a 1536x2560 frame.
PROBLEM_SHORT_NAME = "local_laplacian"
PROBLEM_ARGV = [
    "<RunGenMain>", "--benchmarks=all",
    "--output_extents=[1536,2560,3]",
    "input=zero:[1536,2560,3]", "levels=8", "alpha=1", "beta=1",
]


def _dh(*args):
    subprocess.run([DH_HL, *args], check=True)


def make_catalog():
    _dh("new_catalog", "-C", CATALOG, "seed", PROMPT_MD, GENERATOR, PARAMS)
    # The default problem needs generator set_estimates (which we removed), so
    # disable it and add our explicitly-sized problem instead.
    _dh("disable_problem", "-C", CATALOG, "problem.default")
    _dh("new_problem", "-C", CATALOG, PROBLEM_SHORT_NAME, *PROBLEM_ARGV)
    _dh("experiment", "-C", CATALOG, "begin", LABEL)


def main():
    write_prompt()
    if not HARNESS and GUIDE:      # harness_F_guide_T
        write_guide_contents()
    if not HARNESS:                # both no-harness cells
        write_build_helpers()
    make_catalog()                 # always, last


if __name__ == "__main__":
    main()
'''


if __name__ == "__main__":
    sys.exit(main())
