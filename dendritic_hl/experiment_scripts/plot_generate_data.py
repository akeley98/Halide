#!/usr/bin/env python3

"""
Input: File path to JSON list holding pairs of (catalog dir, session id).
This should have been assembled by the previous script, profiler_session.py

Output: JSON list of experiment objects for passing to plot_render.py.
The experiment object schema is:

    obj["label"] -> label: string
    obj["begin_timestamp"] -> experiment begin timestamp: string
    obj["schedules"][schedule_index] -> schedule: object

The top-level experiments objects are sorted by begin timestamp.

The obj["schedules"] list has one object for each node named by
`dh_hl experiment json_test_schedules` sorted by timestamp,
with "null" cost schedules excluded (likely build failure).

The schedule object schema is:

    sch["id"] -> full ID of node: string
    sch["timestamp"] -> timestamp: string (same format as dh_hl)
    sch["source"] -> C++ source: string
    sch["parameters"] -> JSON generator parameters objects: list of object
    sch["cost"] -> runtime relative to reference schedule: number

Each experiment object also carries a token timeline (from the streaming
launcher's token_log.jsonl, discovered in the `experiment/` dir beside the
catalog) so a schedule timestamp can be translated to a cumulative token count:

    obj["token_timeline"][i] -> object:
        row["utc"] -> ISO-8601 timestamp string.  NOTE: this is a DIFFERENT
            format from dh_hl timestamps (offset "+00:00", not a trailing "Z"),
            so plot_render.py parses it with a separate parser.
        row["cum_input"]          -> cumulative uncached INPUT tokens so far
        row["cum_cache_creation"] -> cumulative cache-creation tokens (these are
            freshly-prefilled input written to the cache -- real new input work)
        row["cum_cache_read"]     -> cumulative cache-read tokens (reused prefix;
            cheap reload, NOT recomputed -- exclude to avoid double counting)
        row["cum_output"]         -> cumulative OUTPUT tokens so far
    Rows are in file order (cumulatives are monotonic non-decreasing).  Empty list
    if no token_log was found.  "Input the model processed" = cum_input +
    cum_cache_creation; output is never cached.

Usage:
    plot_generate_data.py --sessions {input} -o {output}

"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

# ./dh_hl sits one directory up from this script (dendritic_hl/dh_hl).
DH_HL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "dh_hl")

def _dh_hl(args):
    """Run a dh_hl subcommand and return stripped stdout (raises on failure)."""
    out = subprocess.run([DH_HL, *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"dh_hl {' '.join(args)} failed:\n{out.stderr.strip()}")
    return out.stdout.strip()


def _make_schedule_object(catalog_path, session_id, node_id):
    tool = lambda *args: _dh_hl(args + ("-C", catalog_path, "-s", session_id))
    info = json.loads(tool("json_schedule_info", node_id))

    ts = info["timestamp"]
    assert isinstance(ts, str)
    assert len(ts) == 25
    src = info["source"]
    assert isinstance(src, str)
    pa = info["parameters"]
    assert isinstance(pa, list)
    assert all(isinstance(obj, dict) for obj in pa)
    cost = json.loads(tool("json_ranking_cost", node_id))["cost"]

    return dict(id=node_id, timestamp=ts, source=src, parameters=pa, cost=cost)


def _load_token_timeline(catalog_path):
    """Return the token timeline for the experiment owning `catalog_path`.

    token_log.jsonl is written by the streaming launcher into the sibling
    `experiment/` directory next to `catalog.dh_hl`.  Returns a list of
    {"utc","cum_input","cum_output"} rows in file order (monotonic cumulatives),
    or [] (with a warning) if the log is missing.
    """
    exp_dir = os.path.dirname(os.path.abspath(catalog_path))
    tl_path = os.path.join(exp_dir, "experiment", "token_log.jsonl")
    if not os.path.exists(tl_path):
        sys.stderr.write("WARNING: no token_log.jsonl found at {}\n".format(tl_path))
        return []
    rows = []
    with open(tl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            # Keep all four cumulative counters so the renderer can choose the
            # metric.  Note: "new input the model actually processed" is
            # cum_input + cum_cache_creation (cache_creation IS freshly-prefilled
            # input written to the cache); cum_cache_read is reused prefix (cheap
            # reload, not recomputed) and is what should be excluded to avoid
            # counting the whole context on every turn.
            rows.append({"utc": r["utc"],
                         "cum_input": r["cum_input"],
                         "cum_cache_creation": r["cum_cache_creation"],
                         "cum_cache_read": r["cum_cache_read"],
                         "cum_output": r["cum_output"]})
    return rows


def _make_experiment_object(catalog_path, session_id):
    tool = lambda *args: _dh_hl(args + ("-C", catalog_path, "-s", session_id))

    ts = tool("experiment", "get_begin_timestamp")
    label = tool("experiment", "get_begin_label")

    schedules = []
    for node_id in json.loads(tool("experiment", "json_test_schedules")):
        sch = _make_schedule_object(catalog_path, session_id, node_id)
        if sch["cost"] is None:
            sys.stderr.write("Null cost schedule:\n")
            sys.stderr.write("  Catalog: {}\n".format(catalog_path))
            sys.stderr.write("  Session ID: {}\n".format(session_id))
            sys.stderr.write("  Label: {}\n".format(label))
            sys.stderr.write("  Schedule ID: {}\n".format(node_id))
        else:
            schedules.append(sch)
    schedules.sort(key=lambda sch: sch["timestamp"])

    token_timeline = _load_token_timeline(catalog_path)

    return dict(schedules=schedules, begin_timestamp=ts, label=label,
                token_timeline=token_timeline)

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions", metavar="JSON",
                    help="JSON file: list of [catalog, session] pairs")
    ap.add_argument("-o", "--output", help="output JSON path")
    args = ap.parse_args(argv)

    pairs = []
    if args.sessions:
        pairs.extend(tuple(p) for p in json.load(open(args.sessions)))
    else:
        ap.error("Missing --sessions")

    output_path = args.output
    if not output_path:
        ap.error("Empty --output path")

    experiment_objects = []
    seen_catalog_paths = set()

    for (catalog_path, session_id) in pairs:
        experiment_objects.append(_make_experiment_object(catalog_path, session_id))
        if catalog_path in seen_catalog_paths:
            sys.stderr.write("Duplicate catalog path: {}\n".format(catalog_path))
        seen_catalog_paths.add(catalog_path)
    experiment_objects.sort(key=lambda x: x["begin_timestamp"])

    json.dump(experiment_objects, open(output_path, "w"), indent=2)


if __name__ == "__main__":
    main(None)
