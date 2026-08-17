#!/usr/bin/env python3

"""
Input: File path to JSON list holding pairs of (catalog dir, session id).
This should have been assembled by the previous script, profiler_session.py

Output: JSON object for passing to plot_render.py.
It is a list of experiment objects, with schema

    obj["label"] -> label: string
    obj["begin_timestamp"] -> experiment begin timestamp: string
    obj["schedules"][schedule_index] -> schedule: object

The top-level experiments objects are sorted by begin timestamp.

The obj["schedules"] list has one object for each node named by
`dh_hl experiment json_test_schedules` sorted by timestamp,
with "null" cost schedules excluded (likely build failure).

The schedule object schema is

    sch["id"] -> full ID of node: string
    sch["timestamp"] -> timestamp: string (same format as dh_hl)
    sch["source"] -> C++ source: string
    sch["parameters"] -> JSON generator parameters objects: list of object
    sch["cost"] -> runtime relative to reference schedule: number

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

    return dict(schedules=schedules, begin_timestamp=ts, label=label)

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
