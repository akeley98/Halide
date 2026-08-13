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

NOTE: for the no-harness cells `begin_experiment.py` ships `runner.py` (run a
RunGenMain binary with the standard benchmark args); the full `build.py` wrapper
is still TODO.
"""

import argparse
import os
import shutil
import sys


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
    """Return the text of the `begin_experiment.py` to drop in the new dir, read
    from `begin_experiment_template.py` with this experiment's fixed values baked
    in (absolute paths, so the generated script is location-independent).

    Each value is inserted as its `repr()` -- the template's sentinels are bare
    (e.g. `DH_HL = @@DH_HL@@`), so repr supplies the quotes AND the escaping.
    That keeps a path with awkward characters (a Windows backslash, a quote, a
    space) from corrupting the generated source.  The template's @@RUN_ARGS@@
    sentinel is intentionally left untouched -- the generated script substitutes
    it later, itself via repr (write_runner)."""
    subs = {
        "@@LABEL@@": repr(label),
        "@@DH_HL@@": repr(_DH_HL),
        "@@DETAIL_DIR@@": repr(_DETAIL_DIR),
        "@@EXAMPLES_DIR@@": repr(_EXAMPLES_DIR),
    }
    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    for key, value in subs.items():
        text = text.replace(key, value)
    return text


if __name__ == "__main__":
    sys.exit(main())
