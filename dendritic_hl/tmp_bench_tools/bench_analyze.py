#!/usr/bin/env python3
"""Rank schedules into a frontier from bench_driver.py output.

Two orders, two jobs (see the harness design discussion):
  * TOTAL order for navigation: sort by a scalar point estimate (median of the
    robust `min_ms`), deterministic tiebreak. Always comparable.
  * PARTIAL order for decisions: paired-difference significance. Used to (a) flag
    adjacent rank boundaries that are not significant ("possible tie", the star)
    and (b) GATE obsolete-tagging (only a *significantly* cheaper idea may
    obsolete another). A bare point-estimate 'lower cost' is never enough.

All significance is PAIRED by round: within one interleaved round every schedule
is measured under shared system conditions, so differencing within a round
cancels common-mode drift -- which is why marginal-CI overlap is the wrong test
(it ignores the pairing and vastly over-flags).

Placeholder-cost ideas (unimplemented -> inherit parent; implemented-but-
unbenchmarked -> sentinel) must be excluded upstream: they have positional costs,
not measurements. This CSV only contains real benchmarked schedules.
"""
import argparse, csv, random, statistics as st
from collections import defaultdict

random.seed(1)
CONF = 95.0  # confidence level (%) for every CI here


def boot_ci(xs, stat=st.median, B=20000, conf=CONF):
    if len(xs) < 2:
        return (float("nan"), float("nan"))
    n = len(xs)
    reps = sorted(stat([xs[random.randrange(n)] for _ in range(n)])
                  for _ in range(B))
    lo = (100 - conf) / 2
    return (reps[int(lo / 100 * B)], reps[int((100 - lo) / 100 * B)])


def cv(xs):
    m = st.mean(xs)
    return (st.pstdev(xs) / m * 100.0) if m else float("nan")


# ---- pairwise comparison primitives -------------------------------------

def paired_diff_ci(by_round, A, B, conf=CONF):
    """Bootstrap CI of the median per-round difference cost(A) - cost(B).
    Returns (median_diff, lo, hi). Negative => A cheaper/faster than B."""
    diffs = [rd[A] - rd[B] for rd in by_round.values() if A in rd and B in rd]
    if len(diffs) < 2:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = boot_ci(diffs, conf=conf)
    return (st.median(diffs), lo, hi)


def obsoletion_justified(by_round, better, worse, conf=CONF):
    """GATE for obsolete-tagging. True iff `better` is *significantly* cheaper
    than `worse`: the entire paired-difference CI of (better - worse) is below
    zero. Only then may `better` obsolete `worse`."""
    _, _, hi = paired_diff_ci(by_round, better, worse, conf)
    return hi == hi and hi < 0.0  # not-nan and CI entirely negative


def possible_tie(by_round, a, b, conf=CONF):
    """FLAG for the ranked list. True iff the paired-difference CI straddles
    zero (cannot confidently order a and b), or there is insufficient data.
    A tied boundary gets a star, and neither side may obsolete the other."""
    _, lo, hi = paired_diff_ci(by_round, a, b, conf)
    if lo != lo:            # nan -> insufficient data, treat as unresolved
        return True
    return lo <= 0.0 <= hi


# ---- report --------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--metric", choices=["min_ms", "typ_best_ms"],
                    default="min_ms", help="robust cost metric to rank on")
    ap.add_argument("--conf", type=float, default=CONF)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.csv)))
    by_label = defaultdict(list)   # label -> [metric per round]
    by_round = defaultdict(dict)   # round -> {label: metric}
    for r in rows:
        try:
            v = float(r[args.metric])
        except (ValueError, KeyError):
            continue
        by_label[r["label"]].append(v)
        by_round[r["round"]][r["label"]] = v

    med = {lab: st.median(xs) for lab, xs in by_label.items()}
    # TOTAL order: cost then a stable tiebreak (label here; node id in-harness).
    order = sorted(med, key=lambda lab: (med[lab], lab))

    # Star any idea sharing an unresolved (tied) boundary with a neighbour.
    starred = set()
    for a, b in zip(order, order[1:]):
        if possible_tie(by_round, a, b, args.conf):
            starred.update((a, b))

    print(f"metric = {args.metric}   conf = {args.conf:.0f}%   "
          f"rounds = {len(by_round)}\n")
    print("ranked frontier  (lower = better; * = tied with an adjacent rank)")
    for i, lab in enumerate(order):
        lo, hi = boot_ci(by_label[lab], conf=args.conf)
        star = "*" if lab in starred else " "
        print(f"  {i+1}. {star} {lab:18s} {med[lab]:8.4f} ms  "
              f"CI=[{lo:.4f},{hi:.4f}]  CV={cv(by_label[lab]):.2f}%")
        if i < len(order) - 1:
            nxt = order[i + 1]
            d, dlo, dhi = paired_diff_ci(by_round, lab, nxt, args.conf)
            if possible_tie(by_round, lab, nxt, args.conf):
                print(f"       ~~~ possible tie: Δ CI=[{dlo:+.4f},{dhi:+.4f}] "
                      f"spans 0 (order not significant) ~~~")
            else:
                print(f"       vvv {-d:.4f} ms gap, significant: "
                      f"Δ CI=[{dlo:+.4f},{dhi:+.4f}] vvv")

    print("\nobsoletion gate  (row may obsolete col only if significant):")
    for better in order:
        for worse in order:
            if better == worse:
                continue
            if med[better] > med[worse]:
                continue  # only test the cheaper -> dearer direction
            ok = obsoletion_justified(by_round, better, worse, args.conf)
            _, dlo, dhi = paired_diff_ci(by_round, better, worse, args.conf)
            print(f"  {better:18s} -> {worse:18s}  "
                  f"{'OBSOLETE' if ok else 'hold (tie)':10s}  "
                  f"Δ CI=[{dlo:+.4f},{dhi:+.4f}]")


if __name__ == "__main__":
    main()
