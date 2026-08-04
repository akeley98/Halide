#!/usr/bin/env python3
"""Campaign diff: paired runtime verdict for A vs B, then a per-func "what moved"
table aligned by func name (added/removed funcs surfaced, not dropped).

Usage: campaign_diff.py MANIFEST.json LABEL_A LABEL_B
  A is treated as the baseline, B as the change: negative deltas mean B is
  faster / does less.
"""
import argparse, statistics as st
import campaign_lib as cl


def verdict(byr, A, B):
    d, lo, hi = cl.paired_diff_ci(byr, B, A)          # B - A; negative => B faster
    if cl.possible_tie(byr, A, B):
        return f"TIE (Δ CI=[{lo:+.4f},{hi:+.4f}] spans 0)"
    if cl.obsoletion_justified(byr, B, A):
        return f"B significantly FASTER by {-d:.4f} ms (Δ CI=[{lo:+.4f},{hi:+.4f}])"
    return f"B significantly SLOWER by {d:.4f} ms (Δ CI=[{lo:+.4f},{hi:+.4f}])"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("A")
    ap.add_argument("B")
    args = ap.parse_args()

    recs, notes, version, _machine = cl.load_campaign(args.manifest)
    for n in notes:
        print(f"  ! {n}")
    lab = cl.by_label(recs)
    A, B = args.A, args.B
    if not lab.get(A) or not lab.get(B):
        raise SystemExit(f"need records for both {A!r} and {B!r}; "
                         f"have {sorted(lab)}")

    byr = cl.by_round_min(recs, labels={A, B})
    mA = st.median([r.wall_min_ms for r in lab[A]])
    mB = st.median([r.wall_min_ms for r in lab[B]])
    print(f"\nruntime (median wall_min):  A {A} = {mA:.4f} ms   "
          f"B {B} = {mB:.4f} ms")
    print(f"  paired verdict: {verdict(byr, A, B)}")
    print(f"  (paired over {len(byr)} shared rounds)\n")

    fa, fb = cl.agg_funcs(lab[A]), cl.agg_funcs(lab[B])
    names = sorted(set(fa) | set(fb),
                   key=lambda n: -max(fa.get(n, {}).get("time_pct", 0),
                                      fb.get(n, {}).get("time_pct", 0)))
    print("per-func delta (A -> B). ~ = sampled; recompute/tasks exact.")
    print(f"  {'func':24s} {'~time% A->B':>16s}  {'recompute':>14s}  "
          f"{'tasks/run':>14s}")
    for n in names:
        a, b = fa.get(n), fb.get(n)
        if a and b:
            ta, tb = a["time_pct"], b["time_pct"]
            ra = "-" if a["recompute"] is None else f"{a['recompute']:.2f}"
            rb = "-" if b["recompute"] is None else f"{b['recompute']:.2f}"
            pa, pb = a["ptasks_per_run"], b["ptasks_per_run"]
            tag = " " if abs(tb - ta) < 1.0 and pa == pb else "*"
            print(f" {tag}{n:24s} {ta:5.1f}->{tb:5.1f}%   "
                  f"{ra:>6s}->{rb:<6s}  {pa:6.0f}->{pb:<6.0f}")
        elif b:                                   # added by B
            rb = "-" if b["recompute"] is None else f"{b['recompute']:.2f}"
            print(f" +{n:24s} {'(added)':>16s}   {'->':>6s}{rb:<6s}  "
                  f"{'->':>6s}{b['ptasks_per_run']:<6.0f}")
        else:                                     # removed in B
            ra = "-" if a["recompute"] is None else f"{a['recompute']:.2f}"
            print(f" -{n:24s} {'(removed)':>16s}   {ra:>6s}{'->':<6s}  "
                  f"{a['ptasks_per_run']:6.0f}{'->':<6s}")


if __name__ == "__main__":
    main()
