"""Aggregate profiler statistics across benchmarks (the `json_profiler_stats`
tool, idea.md "JSON Profiler Statistics Tool").

Pure and catalog-agnostic: `aggregate` takes a list of profiler pipeline objects
(one per benchmark, all for the *same* schedule + parameters index) plus the
requested pipeline-global (`-p`) and per-function (`-f`) statistic names, and
returns the output JSON object.  Each numeric statistic is summarised across the
benchmarks as ``[25th percentile, median, 75th percentile]``.

We deliberately do NOT hardcode the list of valid statistic names (the profiler
schema evolves): a name is looked up as a key of the pipeline / func object, with
a handful of derived "special" names (ratios and per-run rates) computed instead.
An unknown name or a non-numeric value fails with a clean message, not a
traceback.
"""

import statistics as st

from .errors import DhHlError


def _safe_div(a, b):
    """Ratio, treating a zero denominator as 0.0 (mirrors the profiler's own
    ``+1e-10`` guard; e.g. active_threads with no samples)."""
    return a / b if b else 0.0


def _plain_number(obj, name, kind):
    """Look up *name* as a numeric key of *obj* (a pipeline or func dict).
    Nicely rejects an unknown name or a non-number value (idea.md notes: don't
    enumerate valid names, just fail cleanly)."""
    try:
        v = obj[name]
    except (KeyError, TypeError):
        raise DhHlError("no such {} statistic: {!r}".format(kind, name))
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise DhHlError(
            "{} statistic {!r} is not a number (got {!r})".format(kind, name, v))
    return v


def _pipeline_value(prof, name):
    if name == "active_threads":
        return _safe_div(prof["active_threads_numerator"],
                         prof["active_threads_denominator"])
    if name == "allocs_per_run":
        return _safe_div(prof["num_allocs"], prof["runs"])
    return _plain_number(prof, name, "pipeline")


def _func_value(func, prof, name):
    """A per-function statistic value.  The per-run rates and time_ratio divide
    by the *pipeline*'s counters (a func object has no `runs`/total time)."""
    if name == "active_threads":
        return _safe_div(func["active_threads_numerator"],
                         func["active_threads_denominator"])
    if name == "allocs_per_run":
        return _safe_div(func["num_allocs"], prof["runs"])
    if name == "parallel_loops_per_run":
        return _safe_div(func["parallel_loops"], prof["runs"])
    if name == "parallel_tasks_per_run":
        return _safe_div(func["parallel_tasks"], prof["runs"])
    if name == "time_ratio":
        return _safe_div(func["time_ns"], prof["time_ns"])
    return _plain_number(func, name, "function")


def _percentiles(xs):
    """``[p25, median, p75]`` of *xs* (idea.md).  A single sample degenerates to
    three copies (statistics.quantiles needs >= 2 points)."""
    xs = sorted(xs)
    if len(xs) == 1:
        return [xs[0], xs[0], xs[0]]
    q = st.quantiles(xs, n=4, method="inclusive")
    return [q[0], q[1], q[2]]


def _unique(names):
    """Requested names, de-duplicated, order preserved (idea.md: "one pair for
    each *unique* statistic")."""
    seen, out = set(), []
    for n in names or ():
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def aggregate(profilers, pipeline_stats, func_stats, hottest=None):
    """Build the `json_profiler_stats` output object from *profilers* (the
    per-benchmark pipeline dicts) and the requested `-p`/`-f` names."""
    if not profilers:
        raise DhHlError("no benchmarks found for the requested schedule "
                        "(and parameters)")

    # Func alignment: every benchmark must carry the same funcs (idea.md
    # assertion).  Key by canonical_id; the raw `parent` field is already the
    # parent's canonical_id (-1 for none), so it copies straight through.
    base = profilers[0]["funcs"]
    order = [f["canonical_id"] for f in base]
    name_of = {f["canonical_id"]: f["name"] for f in base}
    parent_of = {f["canonical_id"]: f["parent"] for f in base}
    for prof in profilers:
        funcs = prof["funcs"]
        assert len(funcs) == len(order), "benchmarks disagree on func count"
        for f in funcs:
            assert name_of.get(f["canonical_id"]) == f["name"], \
                "benchmarks disagree on func names"

    out = {}
    for name in _unique(pipeline_stats):
        out[name] = _percentiles([_pipeline_value(p, name) for p in profilers])

    # time_ratio is always included per-func (idea.md), appended if not asked for.
    func_names = _unique(func_stats)
    if "time_ratio" not in func_names:
        func_names.append("time_ratio")

    # Per-benchmark func lookup by canonical_id, built once.
    by_canon = [{f["canonical_id"]: f for f in p["funcs"]} for p in profilers]

    rows = []
    for canon in order:
        row = {"name": name_of[canon], "parent": parent_of[canon],
               "canonical_id": canon}
        for stat in func_names:
            row[stat] = _percentiles(
                [_func_value(by_canon[i][canon], profilers[i], stat)
                 for i in range(len(profilers))])
        rows.append(row)

    # Sort by descending median time_ratio; then optionally keep the n hottest.
    rows.sort(key=lambda r: r["time_ratio"][1], reverse=True)
    if hottest is not None:
        rows = rows[:hottest]
    out["funcs"] = rows
    return out
