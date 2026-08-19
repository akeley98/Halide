#!/usr/bin/env python3
"""Throwaway driver for the LLM Halide scheduling experiment.

Creates a fresh sub-session off the catalog's single terminus, registers an
externally-supplied "anchor" schedule as the benchmark baseline, then profiles
every test schedule (success schedule nodes without an EXPERIMENT IGNORE
commentary) in 8 one-batch passes.

    python3 profiler_session.py {catalog_path} {generator.cpp} {generator_parameters.json}

Optional:
    --log-cli {file.json}    log every dh_hl invocation as a JSON list of argv
                             lists (rewritten after each command, so a failing
                             run still leaves the commands run so far).
    --json-append {list.json}
                             append [catalog_path, session_full_id] to the JSON
                             list in this file (created as [] if absent).

This lives outside dendritic_hl_lib on purpose: it is scaffolding for a specific
experiment and can be deleted wholesale.  It shells out to the real ./dh_hl CLI
rather than importing the library, so it exercises exactly what a human would.

The anchor node doubles as the parent schedule for the profiling sub-session:
it is the one node this script fully controls, so `new_sub_session` never has to
fall back to the parent session's workspace node (which might not resolve).

The experiment query tool is `json_test_schedules`.  The session handle is parsed
from `new_sub_session`'s "Session handle: " line (pinned by
tests/test_sessions.py::test_new_sub_session_handle_line, which names this
script).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid

# ./dh_hl sits one directory up from this script (dendritic_hl/dh_hl).
DH_HL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "dh_hl")

# json_test_schedules skips any success schedule with a non-cancelled commentary
# whose text starts with EXPERIMENT IGNORE:.  The anchor gets that commentary via
# `experiment add_schedule_node --ignore` (which prepends the prefix itself); the
# anchor's duplicate canonical (created by new_sub_session) gets it via a plain
# `comment`, where we supply the full prefixed text.
ANCHOR_IGNORE_TEXT = "anchor schedule for benchmark"
DUP_IGNORE_TEXT = "EXPERIMENT IGNORE: anchor canonical duplicate"

# At least N batches will be done if the cost so far
# is less than profile_cost_thresholds[N - 1].
# Max of len(profile_cost_thresholds)-many profiler runs.
profile_cost_thresholds = [
    None,  # 1
    2.0,   # 2
    2.0,   # 3
    1.25,  # 4
    1.25,  # 5
    1.25,  # 6
    1.00,  # 7
    1.00,  # 8
    1.00,  # 9
]


class CliRunner:
    """Runs ./dh_hl commands, failing the whole script on any non-zero exit and
    (optionally) logging every invocation's argv as a JSON list of lists."""

    def __init__(self, log_path=None):
        self.log_path = log_path
        self._log = []

    def run(self, *args, allow_fail=False):
        argv = [DH_HL, *args]
        # Record before running so a command that fails is still logged.
        self._log.append(list(argv))
        self._flush_log()
        result = subprocess.run(argv, capture_output=True, text=True)
        if result.returncode != 0:
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            msg ="dh_hl command failed ({}): {}".format(result.returncode, " ".join(args))
            if allow_fail:
                sys.stderr.write(msg)
                sys.stderr.write("\n")
            else:
                raise SystemExit(msg)
        return result.stdout

    def _flush_log(self):
        if self.log_path is None:
            return
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(self._log, f, indent=2)


def _line_after(text, prefix):
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise SystemExit("no line starting {!r} in dh_hl output:\n{}".format(
        prefix, text))


def _ignore_node(runner, sess, node_id, full_text):
    """Attach *full_text* (which must start with EXPERIMENT IGNORE:) to *node_id*
    as a negative commentary, so json_test_schedules skips it.  `comment` reads
    its text from a file, so route the text through a throwaway temp file."""
    with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8") as cf:
        cf.write(full_text + "\n")
        comment_path = cf.name
    try:
        runner.run("comment", *sess, comment_path, node_id, "--review", "negative")
    finally:
        os.unlink(comment_path)


def _json_append(path, entry):
    """Append *entry* to the JSON list in *path*, creating it as [] if absent."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise SystemExit("--json-append file does not hold a JSON list: "
                             + path)
    else:
        data = []
    data.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("catalog_path")
    parser.add_argument("generator_path")
    parser.add_argument("parameters_path")
    parser.add_argument("--log-cli", dest="log_cli")
    parser.add_argument("--json-append", dest="json_append")
    args = parser.parse_args()

    runner = CliRunner(args.log_cli)
    # -C is always passed alongside -s: a session full ID requires it, and a
    # handle just has to agree with it (context.resolve_target).
    cat = ["-C", args.catalog_path]

    # Find the single terminus session to parent the profiling sub-session off.
    termini = json.loads(runner.run("list_termini", *cat, "--json"))
    if len(termini) != 1:
        raise SystemExit(
            "expected exactly one terminus in {}, found {}".format(
                args.catalog_path, len(termini)))
    parent_session = termini[0]

    # Register the externally-supplied anchor schedule.  The EXPERIMENT IGNORE
    # commentary keeps it out of the test-schedule list below.
    anchor_id = runner.run(
        "experiment", *cat, "add_schedule_node",
        args.generator_path, args.parameters_path,
        "--ignore", ANCHOR_IGNORE_TEXT).strip()

    # New sub-session dedicated to profiling.  The anchor is used as the parent
    # schedule: it is the one node we created and fully control, avoiding the
    # risk of the default (the parent session's workspace node) not resolving.
    # The proposal file content is irrelevant; it just satisfies the grammar.
    with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8") as pf:
        pf.write("automated profiling session\n")
        proposal_path = pf.name
    try:
        out = runner.run("new_sub_session", *cat, "-s", parent_session,
                         "profiler_session_{}".format(uuid.uuid4().hex),
                         proposal_path, anchor_id)
    finally:
        os.unlink(proposal_path)
    handle = _line_after(out, "Session handle: ")
    print(handle)
    sess = [*cat, "-s", handle]

    runner.run("set_halide_path", *sess, os.path.expanduser("~/Halide"))

    # Record [catalog, session_full_id] early, so we don't do all the profiling
    # work and then die before persisting where the results landed.
    if args.json_append:
        session_id = runner.run("session_full_id", *sess).strip()
        _json_append(args.json_append, [os.path.abspath(args.catalog_path), session_id])

    # Initialize the sub-session's workspace: a sub-session with an
    # uninitialized workspace is off-label usage that may break later tools.
    runner.run("init_workspace", *sess)

    # new_sub_session duplicated the anchor as this session's seed-idea canonical.
    # That duplicate is a major schedule WITHOUT the anchor's ignore commentary,
    # so it would otherwise be profiled as a bogus test schedule.  With the
    # workspace initialized, schedule_full_id (no explicit ID) resolves that
    # canonical; ignore it too.
    dup_id = runner.run("schedule_full_id", *sess).strip()
    _ignore_node(runner, sess, dup_id, DUP_IGNORE_TEXT)

    # Make the anchor the current anchor for this session, then gather the
    # schedules to benchmark: catalog-wide major schedules with no active
    # EXPERIMENT IGNORE commentary (so the anchor and its duplicate are excluded).
    runner.run("set_current_anchor", *sess, anchor_id)
    node_list = json.loads(
        runner.run("experiment", *cat, "json_test_schedules"))

    # Profile one node / one batch at a time (deliberately not using --profile N,
    # N > 1), PROFILE_PASSES times over the whole set.
    counter = 0
    MAX_PROFILE_PASSES = len(profile_cost_thresholds)
    total = MAX_PROFILE_PASSES * len(node_list)
    for batch in range(MAX_PROFILE_PASSES):
        for node in node_list:
            print(f"{counter}/{total}", file=sys.stderr)
            counter += 1
            runner.run("init_build", *sess,
                       "--target", node, "--other", "none")

            if batch > 0:
                cost = json.loads(runner.run("json_ranking_cost", *sess, node))["cost"]
                if cost is None or not (cost < profile_cost_thresholds[batch]):
                    # Trace the code carefully if you think [batch] is wrong.
                    continue

            # Note, no harness sessions can have runtime failures for major schedule nodes.
            # Therefore, I have to forgive failures.
            # Even the json_schedule_info "result" is useless since it
            # checks compile errors only.
            runner.run("build", *sess, "--profile", "1", allow_fail=True)


if __name__ == "__main__":
    main()
