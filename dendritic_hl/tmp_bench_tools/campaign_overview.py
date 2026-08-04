#!/usr/bin/env python3
"""Campaign overview: per-schedule robust runtime + variance + warnings, plus a
compact "hottest funcs" section (the automated 'look at the slowest func' idiom).

Usage: campaign_overview.py MANIFEST.json [--label L]
"""
import argparse, statistics as st
import campaign_lib as cl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--label", default=None, help="restrict to one schedule")
    ap.add_argument("--hot", type=int, default=3, help="# hottest funcs to show")
    args = ap.parse_args()

    recs, notes, version, machine = cl.load_campaign(args.manifest)
    labels = cl.by_label(recs)
    if args.label:
        labels = {args.label: labels.get(args.label, [])}

    print(f"campaign overview   profiler_version={version}   "
          f"records={len(recs)}   schedules={len(labels)}")
    print(f"machine: {machine}  (FYI only; cross-machine comparison out of scope)")
    for n in notes:
        print(f"  ! {n}")
    print("  (time%/threads are SAMPLED [~]; recompute/tasks/mem are exact)\n")

    for label in sorted(labels, key=lambda L: st.median(
            [r.wall_min_ms for r in labels[L]]) if labels[L] else 1e9):
        rs = labels[label]
        if not rs:
            continue
        mins = [r.wall_min_ms for r in rs]
        lo, hi = cl.boot_ci(mins)
        tail = st.median([r.tail_spread_ms() for r in rs])
        total_runs = sum(r.runs for r in rs)
        print(f"== {label} ==")
        print(f"   runtime: median wall_min {st.median(mins):.4f} ms  "
              f"CI=[{lo:.4f},{hi:.4f}]  tail±{tail:.4f}  "
              f"CV={cl.cv(mins):.2f}%  ({len(rs)} records, {total_runs} runs)")

        warns = cl.dedup_warnings(rs)
        if warns:
            print("   warnings:")
            for (rule, func), (cnt, _msg) in sorted(warns.items()):
                print(f"     - [{rule}] {func}  (fired in {cnt}/{len(rs)} records)")
        else:
            print("   warnings: none")

        af = cl.agg_funcs(rs)
        hot = sorted((n for n in af if af[n]["kind"] == 0),
                     key=lambda n: -af[n]["time_pct"])[:args.hot]
        print(f"   hottest funcs:")
        for n in hot:
            f = af[n]
            rec = "-" if f["recompute"] is None else f"{f['recompute']:.2f}"
            print(f"     ~{f['time_pct']:5.1f}%  {n:22s} "
                  f"~thr {f['threads']:4.1f} | recompute {rec} "
                  f"tasks/run {f['ptasks_per_run']:.0f} "
                  f"loops/run {f['ploops_per_run']:.1f}")
        print()


if __name__ == "__main__":
    main()
