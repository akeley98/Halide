"""Cost model core: rank and compare schedules from batched profiler samples.

This is the shared computation behind `json_ranking_cost`, `json_compare_cost`,
and the `list_private_ideas` frontier (idea.md "Cost Comparison Methodology").
It is deliberately catalog-agnostic: it consumes the *cached* benchmark-set
statistics (SessionWorkspace.read_private_benchmark_sets) and returns plain
dicts; the tool layer resolves anchors/short IDs and expands per-parameter lists.

Method, in one breath: rank on the robust `wall_time_min` (fastest run of a
record, ~1% CV -- the mean is outlier-contaminated), and decide regressions by a
*paired* test.  Pairing is by *batch*: schedules profiled together in one
interleaved build run share system drift, so differencing within a batch cancels
common-mode noise -- which is exactly why the build tool interleaves batches, and
why marginal-CI overlap would be the wrong test (it ignores the pairing and
over-flags).  The paired-difference significance uses a bootstrap percentile CI
of the median difference; it is stdlib-only and, with a fixed RNG seed,
deterministic (hence testable).  See impl.md "Cost Model Core" for the details.

This approach and its primitives were ported from the reference campaign tooling
in `tmp_bench_tools/bench_analyze.py`.
"""

import math
import random
import statistics as st
from collections import defaultdict

from .catalog import EXPECTED_PROFILER_VERSION

# Confidence level for the 2-way comparison CI (idea.md defaults 2-way to 95%).
DEFAULT_CONFIDENCE = 0.95
# Bootstrap replicate count.  Reduced from the reference's 20000: these tools run
# in the agent's interactive loop (the frontier does many pairwise checks) and a
# few thousand reps already give a stable percentile at our sample sizes.  Shared
# by json_compare_cost and the frontier so the documented "the frontier uses
# json_compare_cost" equivalence holds exactly (idea.md).
DEFAULT_BOOTSTRAP = 4000
# Fixed so a given set of samples always yields the same CI (and thus the same
# improvement/regression/unknown verdict) -- reproducibility over raw precision.
_BOOTSTRAP_SEED = 1


class CostData:
    """Raw `wall_time_min` cost samples grouped by (schedule, parameters index)
    and by *batch*, reachable from a session's private benchmark set list.

    A batch key is ``(benchmark set full id, batch index)``: two schedules are
    "in the same batch" only if one build run profiled them together at the same
    batch index, so their difference cancels common-mode drift.  Only benchmark
    sets recorded at `EXPECTED_PROFILER_VERSION` contribute (a mismatched set is
    a "can't compare" record and is skipped)."""

    def __init__(self):
        # _samples[schedule id][params index] = {batch_key: wall_time_min}
        self._samples = defaultdict(lambda: defaultdict(dict))

    @classmethod
    def from_private_sets(cls, private_sets):
        """Build from ``{set id: cache}`` (SessionWorkspace cache shape, see
        impl.md "Private Benchmark Sets on Disk")."""
        self = cls()
        for set_id, cache in private_sets.items():
            if cache.get("profiler_version") != EXPECTED_PROFILER_VERSION:
                continue  # incompatible profiler schema: skip whole set
            for sched_id, cells in cache.get("schedules", {}).items():
                for pidx, cell in enumerate(cells):
                    for batch, v in enumerate(cell.get("wall_time_min", [])):
                        if v is not None:
                            self._samples[sched_id][pidx][(set_id, batch)] = v
        return self

    # -- batch / sample introspection -----------------------------------
    def batches_of(self, sched_id):
        """Set of batch keys in which *sched_id* was profiled (any params)."""
        keys = set()
        for per in self._samples.get(sched_id, {}).values():
            keys |= set(per)
        return keys

    def _median_over(self, sched_id, pidx, batch_keys):
        per = self._samples.get(sched_id, {}).get(pidx, {})
        xs = [per[k] for k in batch_keys if k in per]
        return st.median(xs) if xs else None

    def raw_costs(self, sched_id, batch_keys):
        """``{params index: median raw cost over batch_keys}`` (value None for a
        params index with no sample in *batch_keys*)."""
        return {pidx: self._median_over(sched_id, pidx, batch_keys)
                for pidx in self._samples.get(sched_id, {})}

    def representative(self, sched_id, batch_keys):
        """``(rep params index, raw_costs)``.  The representative is the params
        index of lowest median raw cost over *batch_keys* (idea.md "Cost
        Comparison Methodology"); None if no params index has a sample there.
        Ties break toward the lower index (deterministic)."""
        costs = self.raw_costs(sched_id, batch_keys)
        rep = None
        for pidx in sorted(costs):
            c = costs[pidx]
            if c is not None and (rep is None or c < costs[rep]):
                rep = pidx
        return rep, costs

    # -- the two methodologies ------------------------------------------
    def ranking_cost(self, target_id, anchor_id):
        """Cost of *target_id* by "Cost Ranking" (idea.md), with or without an
        anchor.  Returns a dict: ``batch_count``, ``cost`` (number/None),
        ``anchor`` (id/None), ``representative`` (params index/None), and
        ``raw_costs`` (``{params index: raw cost}``, for the caller to expand
        into the ordered ``parameters_raw_cost`` list)."""
        if anchor_id is None:
            keys = self.batches_of(target_id)
            rep, costs = self.representative(target_id, keys)
            cost = costs.get(rep) if rep is not None else None
            return {"batch_count": len(keys), "cost": cost, "anchor": None,
                    "representative": rep, "raw_costs": costs}
        keys = self.batches_of(target_id) & self.batches_of(anchor_id)
        t_rep, t_costs = self.representative(target_id, keys)
        a_rep, _ = self.representative(anchor_id, keys)
        cost = None
        if t_rep is not None and a_rep is not None:
            t_per = self._samples[target_id][t_rep]
            a_per = self._samples[anchor_id][a_rep]
            ratios = [t_per[k] / a_per[k] for k in keys
                      if k in t_per and k in a_per and a_per[k]]
            cost = st.median(ratios) if ratios else None
        return {"batch_count": len(keys), "cost": cost, "anchor": anchor_id,
                "representative": t_rep, "raw_costs": t_costs}

    def compare(self, lhs_id, rhs_id, confidence=DEFAULT_CONFIDENCE,
                bootstrap=DEFAULT_BOOTSTRAP):
        """Head-to-head "2-way Cost Comparison" of LHS vs RHS (idea.md).  Returns
        a dict: ``batch_count``, ``result`` (improvement/regression/unknown),
        ``lhs_raw_cost``/``lhs_representative``, ``rhs_raw_cost``/
        ``rhs_representative``.  ``improvement`` means LHS is confidently cheaper
        than RHS; ``regression`` means confidently dearer."""
        keys = self.batches_of(lhs_id) & self.batches_of(rhs_id)
        l_rep, l_costs = self.representative(lhs_id, keys)
        r_rep, r_costs = self.representative(rhs_id, keys)
        diffs = []
        if l_rep is not None and r_rep is not None:
            l_per = self._samples[lhs_id][l_rep]
            r_per = self._samples[rhs_id][r_rep]
            diffs = [l_per[k] - r_per[k] for k in keys
                     if k in l_per and k in r_per]
        _, lo, hi = paired_diff_ci(diffs, confidence, bootstrap)
        return {"batch_count": len(diffs), "result": compare_verdict(lo, hi),
                "lhs_raw_cost": l_costs.get(l_rep) if l_rep is not None else None,
                "lhs_representative": l_rep,
                "rhs_raw_cost": r_costs.get(r_rep) if r_rep is not None else None,
                "rhs_representative": r_rep}

    def is_improvement(self, lhs_id, rhs_id, confidence=DEFAULT_CONFIDENCE,
                       bootstrap=DEFAULT_BOOTSTRAP):
        """True iff LHS confidently improves over RHS (the obsoletion gate used
        by the list_private_ideas frontier, idea.md "Obsoleted By")."""
        return self.compare(lhs_id, rhs_id, confidence, bootstrap)["result"] \
            == "improvement"


# ---- bootstrap significance primitives ------------------------------------

def paired_diff_ci(diffs, confidence=DEFAULT_CONFIDENCE,
                   bootstrap=DEFAULT_BOOTSTRAP):
    """Bootstrap percentile CI of the *median* of *diffs* (the per-batch paired
    differences).  Returns ``(median, lo, hi)``; ``(nan, nan, nan)`` for fewer
    than 2 samples.  Deterministic: a fixed-seed local RNG, so equal input always
    yields an equal CI."""
    n = len(diffs)
    if n < 2:
        return (math.nan, math.nan, math.nan)
    rng = random.Random(_BOOTSTRAP_SEED)
    reps = sorted(st.median([diffs[rng.randrange(n)] for _ in range(n)])
                  for _ in range(bootstrap))
    tail = (1.0 - confidence) / 2.0
    lo = reps[int(tail * bootstrap)]
    hi = reps[min(int((1.0 - tail) * bootstrap), bootstrap - 1)]
    return (st.median(diffs), lo, hi)


def compare_verdict(lo, hi):
    """Map a paired-difference CI of ``cost(LHS) - cost(RHS)`` to a verdict.
    Entirely below zero -> LHS cheaper -> ``improvement``; entirely above zero ->
    ``regression``; straddling zero or nan (too little data) -> ``unknown``
    (idea.md "2-way Cost Comparison")."""
    if lo != lo or hi != hi:  # nan: insufficient data
        return "unknown"
    if lo < 0 and hi < 0:
        return "improvement"
    if lo > 0 and hi > 0:
        return "regression"
    return "unknown"
