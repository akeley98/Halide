#!/usr/bin/env python3
"""Shared loader + aggregation for campaign presentation tools.

Input is a *manifest* (JSON) that references, per profile invocation ("record"):
  * profiler_json  : the raw HL_PROFILER_JSON_OUTPUT file ({"pipelines":[obj]},
                     or a bare pipeline object -- both accepted)
  * warnings_json  : the HL_PROFILER_JSON_TEMPORARY_WARNINGS JSONL file
and the metadata the raw files lack: label (schedule identity), round (interleave
batch, the pairing key), hostname, cpu_count.

    { "campaign": "...",
      "records": [ {"label","round","hostname","cpu_count",
                    "profiler_json","warnings_json"}, ... ] }

This manifest is the drop-in seam: a real harness swaps this loader for a catalog
query returning the same per-record objects. Everything downstream is unchanged.

Trust calibration (used by every tool): per-func TIME and ACTIVE-THREADS are
sampling-based (noisy per record, but converge when pooled across the campaign's
many records); recompute_ratio / parallel_loops / parallel_tasks / mem / allocs
are EXACT counters. Tools mark the two groups differently.
"""
import json, math, os, sys, statistics as st
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_analyze import (boot_ci, cv, paired_diff_ci,      # noqa: F401
                           obsoletion_justified, possible_tie)

UINT64_MAX = (1 << 64) - 1
SAMPLED_COLS = {"time_pct", "threads"}   # everything else per-func is exact


def _load_profiler(path):
    d = json.load(open(path))
    return d["pipelines"][0] if isinstance(d, dict) and "pipelines" in d else d


def _load_warnings(path):
    if not path or not os.path.exists(path):
        return []
    out = []
    for line in open(path):
        line = line.strip()
        if line:
            out.extend(json.loads(line).get("warnings", []))
    return out


class Record:
    def __init__(self, e, base):
        self.label = e["label"]
        self.round = e["round"]
        self.hostname = e.get("hostname")
        self.cpu_count = e.get("cpu_count")
        self.prof = _load_profiler(os.path.join(base, e["profiler_json"]))
        self.warnings = _load_warnings(
            os.path.join(base, e["warnings_json"]) if e.get("warnings_json") else None)
        self.version = self.prof.get("profiler_version")
        self.runs = self.prof["runs"]

    def _sm(self):
        return [v / 1e6 for v in self.prof["wall_time_smallest"] if v != UINT64_MAX]

    @property
    def wall_min_ms(self):
        return self.prof["wall_time_min"] / 1e6

    @property
    def wall_mean_ms(self):
        return self.prof["wall_time_mean"] / 1e6

    def wall_std_ms(self):
        return math.sqrt(max(self.prof["wall_time_m2"] / self.runs, 0)) / 1e6

    def typ_best_ms(self):
        sm = self._sm()
        return st.median(sm) if sm else self.wall_min_ms

    def tail_spread_ms(self):
        sm = self._sm()
        return (sm[-1] - sm[0]) if len(sm) > 1 else 0.0


def stable_machine_name():
    """Network-stable, human-readable machine name for FYI display only.
    NOT a comparability key: deciding whether two machines are "close enough"
    to pool (GPU, memory bandwidth/capacity, power mode, ambient temp, ...) is
    out of scope for the prototype, so cross-machine aggregation is deferred.
    `socket.gethostname()` is avoided on purpose -- it tracks the DHCP/mDNS name
    and drifts across networks (e.g. Davids-MacBook-Pro.local <-> Mac.lan)."""
    import platform, subprocess
    if platform.system() == "Darwin":
        try:
            n = subprocess.run(["scutil", "--get", "LocalHostName"],
                               capture_output=True, text=True).stdout.strip()
            if n:
                return n
        except Exception:
            pass
    if os.path.exists("/etc/hostname"):
        try:
            n = open("/etc/hostname").read().strip()
            if n:
                return n
        except Exception:
            pass
    return platform.node() or "unknown"


def load_campaign(manifest_path):
    """Returns (records, notes, expected_version, machine). The only
    comparability gate is profiler_version (same-machine, version-mismatch is
    discarded). Cross-machine gating is out of scope; `machine` is FYI only."""
    man = json.load(open(manifest_path))
    base = os.path.dirname(os.path.abspath(manifest_path))
    recs = [Record(e, base) for e in man["records"]]
    notes = []
    versions = [r.version for r in recs if r.version is not None]
    expected = st.mode(versions) if versions else None
    kept = []
    for r in recs:
        if r.version != expected:
            notes.append(f"DISCARD {r.label} r{r.round}: profiler_version "
                         f"{r.version} != expected {expected}")
        else:
            kept.append(r)
    machine = man.get("machine", "unknown")
    return kept, notes, expected, machine


def by_label(recs):
    d = defaultdict(list)
    for r in recs:
        d[r.label].append(r)
    return d


def by_round_min(recs, labels=None):
    """round -> {label: wall_min_ms}, for paired significance."""
    d = defaultdict(dict)
    for r in recs:
        if labels is None or r.label in labels:
            d[r.round][r.label] = r.wall_min_ms
    return d


def agg_funcs(recs):
    """Pool per-func stats across a label's records, keyed by func NAME
    (canonical_id is stable within a schedule, but NAME is the cross-schedule
    key used by diff; same-name funcs are merged). Returns {name: stats}, where
    time_pct/threads are SAMPLED (pooled -> converged) and the rest are exact."""
    a = defaultdict(lambda: {"t": 0.0, "atn": 0.0, "atd": 0.0, "rec": [],
                             "pl": 0, "pt": 0, "runs": 0, "kind": None, "mem": 0})
    total = 0.0
    for r in recs:
        for f in r.prof["funcs"]:
            g = a[f["name"]]
            g["t"] += f["time_ns"]; total += f["time_ns"]
            g["atn"] += f["active_threads_numerator"]
            g["atd"] += f["active_threads_denominator"]
            if f.get("recompute_ratio") is not None:
                g["rec"].append(f["recompute_ratio"])
            g["pl"] += f.get("parallel_loops", 0)
            g["pt"] += f.get("parallel_tasks", 0)
            g["runs"] += r.runs
            g["kind"] = f["kind"]
            g["mem"] = max(g["mem"], f.get("memory_peak", 0))
    out = {}
    for name, g in a.items():
        out[name] = {
            "time_pct": 100 * g["t"] / total if total else 0.0,
            "threads": g["atn"] / g["atd"] if g["atd"] else 0.0,
            "recompute": st.median(g["rec"]) if g["rec"] else None,
            "ptasks_per_run": g["pt"] / g["runs"] if g["runs"] else 0.0,
            "ploops_per_run": g["pl"] / g["runs"] if g["runs"] else 0.0,
            "mem_peak": g["mem"], "kind": g["kind"],
        }
    return out


def dedup_warnings(recs):
    """(rule, func) -> #records it fired in, plus a sample message."""
    seen = defaultdict(lambda: [0, ""])
    for r in recs:
        for w in {(w["rule"], w["func"]): w for w in r.warnings}.values():
            k = (w["rule"], w["func"])
            seen[k][0] += 1
            seen[k][1] = w["message"]
    return seen
