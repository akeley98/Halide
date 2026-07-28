"""Isolation layer for the *temporary* profiler-warnings delivery hack.

Andrew Adams's rewritten profiler does not (yet) put warnings in its main JSON
output, so today they reach us through a side channel we fully expect to REPLACE
(see reference_build_commands.md "Warnings Output" and the impl.md "Warning
Delivery Hack" inventory).  Every assumption about how warnings are delivered,
stored, and shaped is funneled through THIS module so that when a better
mechanism arrives the blast radius is here plus its handful of call sites --
rather than smeared across build.py, catalog.py, and tools.py.

A "warning object" is a plain dict as delivered by the profiler, currently with
keys `rule`, `func`, `message`, `canonical_id` (and possibly more we ignore).
Future, more holistic "view benchmark" tools that need warnings should go through
these helpers too, not re-parse the benchmark JSON themselves.
"""

import json
import os


# -- ingestion: profiler side channel -> list of warning objects ------------

def warnings_from_temp_file(path):
    """Parse the ``HL_PROFILER_JSON_TEMPORARY_WARNINGS`` side-channel file and
    return its inner ``warnings`` list.  The file is nominally "JSON lines", but
    the harness requires one generator per file, so we parse it as a single JSON
    object.  Returns ``[]`` if the file is absent (the profiler writes it only
    when there are warnings) or carries no warnings."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return []
    obj = json.loads(text)
    return list(obj.get("warnings", []))


# -- storage: benchmark JSON object -> list of warning objects --------------

def warnings_of_benchmark(benchmark_data):
    """The warning objects stored in a benchmark JSON object.  They live under a
    top-level ``warnings`` key, SEPARATE from the ``profiler`` object (the current
    hack; ideally they'd be integrated into the profiler payload).  ``[]`` for
    older benchmarks that predate warnings."""
    return list(benchmark_data.get("warnings", []))


# -- individual warning-object field accessors ------------------------------
# Centralized so a change to the delivered warning shape touches only these.

def warning_rule(w):
    return w.get("rule")


def warning_func(w):
    return w.get("func")


def warning_message(w):
    return w.get("message", "")


def warning_key(w):
    """The ``(rule, func)`` identity a ``WarningToggle`` blocks on (idea.md
    "WarningToggle State").  NB func names can collide within a pipeline; the
    prototype ignores that (see reference_build_commands.md)."""
    return (warning_rule(w), warning_func(w))
