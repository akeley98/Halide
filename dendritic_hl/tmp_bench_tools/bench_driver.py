#!/usr/bin/env python3
"""Interleaved benchmark driver (JSON-native).

Runs N rounds; within each round it visits the schedules in a *shuffled* order
(restore -> profile), so slow system drift (thermal, background load) is spread
across all schedules rather than biasing one. All statistics come from the
persisted benchmark JSON (dh_hl json_schedule_info) -- nothing is scraped from
stdout.

Per profile the profiler now reports directly-measured per-run wall-clock stats
over ALL runs (see profiler_common.cpp): wall_time_min/max, a Welford mean/m2,
and the K smallest per-run durations. From those we derive:

  min         : fastest single run  -- the low-noise comparison stat
  typ_best    : median of the K smallest runs -- denoised best case
  tail_spread : (kth smallest - min) -- an outlier-robust error bar on the best
  mean / std  : Welford mean and sqrt(m2/runs) -- observability only (the mean is
                outlier-contaminated; kept to flag noisy runs, not to compare on)

One CSV row per profile, flushed immediately, so a long run is safe to interrupt.
"""
import argparse, csv, json, math, random, statistics, subprocess, sys, time

# label -> dh_hl schedule short id
SCHEDULES = [
    ("opus_no_peek",     "9f3979.vectorize_hist_colsum.canon"),
    ("opus_hist_unroll", "7bdae8.borrow_hist_unroll.canon"),
    ("answer_key",       "root.4fba7d"),
    ("answer_key_wfix",  "4fba7d.answerkey_width_fix.canon"),
]
CATALOG = "hist.dh_hl"
UINT64_MAX = (1 << 64) - 1


def dh(*args, handle=None):
    cmd = ["dh_hl", *args] + (["-s", handle] if handle else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def json_wall(sid):
    """min, mean, std, runs, typ_best, tail_spread (ms) from the latest record."""
    rc, out = dh("json_schedule_info", "-C", CATALOG, sid)
    p = json.loads(out)["benchmark"][-1]["profiler"]
    runs = p["runs"]
    mn = p["wall_time_min"] / 1e6
    mean = p["wall_time_mean"] / 1e6
    std = math.sqrt(max(p["wall_time_m2"] / runs, 0)) / 1e6
    small = [v / 1e6 for v in p["wall_time_smallest"] if v != UINT64_MAX]
    typ = statistics.median(small) if small else mn
    tail = (small[-1] - small[0]) if len(small) > 1 else 0.0
    return mn, mean, std, runs, typ, tail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", required=True)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--out", default="bench_tools/results.csv")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    random.seed(args.seed)

    f = open(args.out, "w", newline="")
    w = csv.writer(f)
    w.writerow(["round", "seq", "epoch", "label", "id",
                "min_ms", "typ_best_ms", "tail_spread_ms",
                "mean_ms", "std_ms", "runs"])
    f.flush()
    seq = 0
    for rnd in range(args.rounds):
        order = SCHEDULES[:]
        random.shuffle(order)
        for label, sid in order:
            rc, _ = dh("restore_schedule", sid, handle=args.handle)
            if rc:
                print("restore failed", sid, file=sys.stderr)
                sys.exit(1)
            dh("profile", handle=args.handle)          # side effect: writes JSON
            mn, mean, std, runs, typ, tail = json_wall(sid)
            w.writerow([rnd, seq, f"{time.time():.3f}", label, sid,
                        f"{mn:.5f}", f"{typ:.5f}", f"{tail:.5f}",
                        f"{mean:.5f}", f"{std:.5f}", runs])
            f.flush()
            print(f"r{rnd:02d} {label:18s} min={mn:.4f} typ_best={typ:.4f} "
                  f"(+{tail:.4f}) mean={mean:.4f}±{std:.4f} runs={runs}",
                  flush=True)
            seq += 1
    f.close()
    print(f"\nwrote {seq} rows to {args.out}")


if __name__ == "__main__":
    main()
