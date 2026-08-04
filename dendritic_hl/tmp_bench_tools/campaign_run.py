#!/usr/bin/env python3
"""Run an interleaved comparison campaign and write the drop-in file layout:
one raw profiler JSON + one warnings JSONL per invocation, plus a manifest.

Stand-in for the eventual harness campaign: it profiles each schedule once per
round (shuffled order), capturing the main profiler JSON (via json_schedule_info,
rewrapped as {"pipelines":[...]}) and the temporary warnings JSONL (via the
HL_PROFILER_JSON_TEMPORARY_WARNINGS env var, which the harness passes through).
"""
import argparse, json, os, random, subprocess
import campaign_lib as cl

SCHEDULES = [
    ("opus_no_peek",     "9f3979.vectorize_hist_colsum.canon"),
    ("opus_hist_unroll", "7bdae8.borrow_hist_unroll.canon"),
    ("answer_key",       "root.4fba7d"),
    ("answer_key_wfix",  "4fba7d.answerkey_width_fix.canon"),
]
CATALOG = "hist.dh_hl"


def dh(*a, handle=None, env=None):
    cmd = ["dh_hl", *a] + (["-s", handle] if handle else [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", required=True)
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--out-dir", default="bench_tools/campaign")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    random.seed(args.seed)
    os.makedirs(args.out_dir, exist_ok=True)

    records = []
    for rnd in range(args.rounds):
        order = SCHEDULES[:]
        random.shuffle(order)
        for label, sid in order:
            wname = f"r{rnd:02d}_{label}.warnings.jsonl"
            pname = f"r{rnd:02d}_{label}.profiler.json"
            wabs = os.path.abspath(os.path.join(args.out_dir, wname))
            env = dict(os.environ)
            env["HL_PROFILER_JSON_TEMPORARY_WARNINGS"] = wabs
            dh("restore_schedule", sid, handle=args.handle)
            dh("profile", handle=args.handle, env=env)          # warnings -> wabs
            rec = json.loads(dh("json_schedule_info", "-C", CATALOG, sid).stdout)
            b = rec["benchmark"][-1]
            json.dump({"pipelines": [b["profiler"]]},
                      open(os.path.join(args.out_dir, pname), "w"))
            records.append({
                "label": label, "round": rnd,
                "hostname": b.get("hostname"), "cpu_count": b.get("cpu_count"),
                "profiler_json": pname, "warnings_json": wname,
            })
            print(f"r{rnd:02d} {label:18s} "
                  f"min={b['profiler']['wall_time_min']/1e6:.4f}ms", flush=True)

    mpath = os.path.join(args.out_dir, "manifest.json")
    json.dump({"campaign": "hist_demo",
               "machine": cl.stable_machine_name(),   # stable FYI name
               "records": records},
              open(mpath, "w"), indent=1)
    print(f"\nwrote {len(records)} records + {mpath}")


if __name__ == "__main__":
    main()
