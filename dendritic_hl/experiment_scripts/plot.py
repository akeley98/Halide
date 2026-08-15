#!/usr/bin/env python
"""Plot best-schedule cost vs. time from one or more dendritic_hl catalogs.

Throwaway helper for the LLM Halide scheduling experiment (see plot.md).

For each (catalog, session) pair it draws the running-best relative cost as a
function of wall-clock time since the experiment's begin timestamp:

    f(t) = min(node.cost for node in json_test_schedules if node.timestamp <= t)

which renders as downward steps.  x is linear seconds, y is log cost, and a
horizontal reference line is drawn at cost = 1 (runtime of the reference
schedule).  Multiple pairs are overlaid, colour/symbol coded by the catalog's
experiment label.

Usage:
    plot.py CATALOG SESSION [CATALOG SESSION ...] [-o out.png]
    plot.py --sessions sessions.json [-o out.png]

`sessions.json` is a JSON list of [catalog_path, session_id] pairs.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

# dendritic_hl timestamp format, e.g. "2026-08-13T222943_452308Z".
_TS_FMT = "%Y-%m-%dT%H%M%S_%fZ"
# A schedule node full ID is "{timestamp}_{sha256hex}".
_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{6}_\d{6}Z)_([0-9a-f]{64})$")

# Color + marker per experiment label
# Gold o: Guide true (purple x, guide false)
# Heavy/darker: Harness true (lighter, harness false)
_LABEL_STYLE = {
    "harness_T_guide_T": ("#957c00", "o"),
    "harness_T_guide_F": ("#7c007c", "x"),
    "harness_F_guide_T": ("#e1bc00", "o"),
    "harness_F_guide_F": ("#e800e8", "x"),
}
# Fallback styles for unrecognised labels.
_FALLBACK_STYLE = [
    ("#7c3aed", "v"), ("#0891b2", "P"), ("#be185d", "X"), ("#4b5563", "*"),
]


def _find_dh_hl(explicit):
    """Locate the dh_hl executable (explicit arg, PATH, then ~/.local/bin)."""
    if explicit:
        return explicit
    found = shutil.which("dh_hl")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/dh_hl")
    if os.path.exists(fallback):
        return fallback
    sys.exit("error: dh_hl not found on PATH; pass --dh-hl PATH")


def _run(dh_hl, args):
    """Run a dh_hl subcommand and return stripped stdout (raises on failure)."""
    out = subprocess.run([dh_hl, *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"dh_hl {' '.join(args)} failed:\n{out.stderr.strip()}")
    return out.stdout.strip()


def _parse_ts(text):
    return datetime.strptime(text, _TS_FMT)


def _node_timestamp(full_id):
    """Extract the datetime embedded in a schedule node full ID."""
    m = _ID_RE.match(full_id)
    if not m:
        raise ValueError(f"unexpected schedule id: {full_id!r}")
    return _parse_ts(m.group(1))


def _style_for(label, seen):
    """Return a (colour, marker) for an experiment label, stable per run."""
    if label in _LABEL_STYLE:
        return _LABEL_STYLE[label]
    if label not in seen:
        seen[label] = _FALLBACK_STYLE[len(seen) % len(_FALLBACK_STYLE)]
    return seen[label]


def collect_series(dh_hl, catalog, session):
    """Return a dict describing one (catalog, session) series, or None if empty.

    Keys: label, session, points (sorted list of (t_seconds, cost)).
    """
    begin = _parse_ts(_run(dh_hl, ["experiment", "-C", catalog, "get_begin_timestamp"]))
    try:
        label = _run(dh_hl, ["experiment", "-C", catalog, "get_begin_label"])
    except RuntimeError:
        label = "unknown"

    ids = json.loads(_run(dh_hl, ["experiment", "-C", catalog, "json_test_schedules"]))

    points = []
    for nid in ids:
        info = json.loads(_run(dh_hl, ["json_ranking_cost", "-s", session, "-C", catalog, nid]))
        cost = info.get("cost")
        if cost is None:  # not co-benchmarked with the anchor -> undefined here.
            continue
        t = (_node_timestamp(nid) - begin).total_seconds()
        points.append((t, float(cost)))

    if not points:
        return None
    points.sort(key=lambda p: p[0])
    return {"label": label, "session": session, "points": points}


def plot_series(ax, series, style, x_end, show_scatter=False):
    """Draw one running-best step line; return its endpoints for annotation.

    Returns {colour, start=(t, cost), finish=(t, cost)}.  The optional faint
    scatter of *every* evaluated schedule is drawn only when show_scatter.
    """
    colour, marker = style
    ts = [p[0] for p in series["points"]]
    costs = [p[1] for p in series["points"]]

    # Running best-so-far (non-increasing); mark strict improvements.
    best, running = float("inf"), []
    improve_t, improve_c = [], []
    for t, c in zip(ts, costs):
        if c < best:
            best = c
            improve_t.append(t)
            improve_c.append(best)
        running.append(best)

    # Extend the final plateau to the right edge of the whole chart.
    step_t = list(ts) + [x_end]
    step_c = running + [running[-1]]

    short = series["session"].split("_")[0] + "@" + series["session"][:19]
    legend = f"{series['label']}  [{short}]"

    ax.step(step_t, step_c, where="post", color=colour, lw=1.8, zorder=3, label=legend)
    # Markers on the down-step corners (the new-record schedules).
    ax.scatter(improve_t, improve_c, color=colour, marker=marker, s=55,
               zorder=4, edgecolors="white", linewidths=0.6)
    if show_scatter:
        # Faint context: every evaluated schedule.  Off by default -- it clutters
        # charts that overlay many sessions.
        ax.scatter(ts, costs, color=colour, marker=marker, s=18, alpha=0.22, zorder=2)

    return {"colour": colour, "start": (ts[0], running[0]), "finish": (x_end, running[-1])}


def annotate_endpoints(ax, drawn):
    """Label each series' start and finish cost.  ymin/ymax are honoured, so a
    start that sits above the top is pinned just under the top edge."""
    lo, hi = ax.get_ylim()
    fmt = lambda c: f"{c:.3g}×"  # e.g. "11.1x", "0.935x"
    for d in drawn:
        colour = d["colour"]
        sx, sc = d["start"]
        fx, fc = d["finish"]
        if sc > hi:  # off the top -> pin the label just inside the top edge
            ax.annotate(fmt(sc), (sx, hi), xytext=(4, -4), textcoords="offset points",
                        ha="left", va="top", fontsize=8, fontweight="bold", color=colour)
        else:
            ax.annotate(fmt(sc), (sx, sc), xytext=(4, 5), textcoords="offset points",
                        ha="left", va="bottom", fontsize=8, fontweight="bold", color=colour)
        ax.annotate(fmt(fc), (fx, fc), xytext=(-4, 5), textcoords="offset points",
                    ha="right", va="bottom", fontsize=8, fontweight="bold", color=colour)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pairs", nargs="*", metavar="CATALOG SESSION",
                    help="one or more catalog/session pairs")
    ap.add_argument("--sessions", metavar="JSON",
                    help="JSON file: list of [catalog, session] pairs")
    ap.add_argument("-o", "--output", default="chart.png", help="output image path")
    ap.add_argument("--ymin", type=float, default=None,
                    help="minimum cost shown on the (linear) y-axis")
    ap.add_argument("--ymax", type=float, default=None,
                    help="maximum cost shown on the y-axis; steps above this run off the top")
    ap.add_argument("--title", default="Best schedule cost over time")
    ap.add_argument("--endpoints", action="store_true",
                    help="label each line's start and finish cost (off by default)")
    ap.add_argument("--scatter", action="store_true",
                    help="faintly plot every evaluated schedule, not just records (off by default)")
    ap.add_argument("--dh-hl", default=None, help="path to the dh_hl executable")
    ap.add_argument("--show", action="store_true", help="display the figure interactively")
    args = ap.parse_args(argv)

    pairs = []
    if args.sessions:
        pairs.extend(tuple(p) for p in json.load(open(args.sessions)))
    if args.pairs:
        if len(args.pairs) % 2 != 0:
            ap.error("positional pairs must come in (CATALOG SESSION) twos")
        it = iter(args.pairs)
        pairs.extend(zip(it, it))
    if not pairs:
        ap.error("provide catalog/session pairs or --sessions JSON")

    dh_hl = _find_dh_hl(args.dh_hl)

    import matplotlib
    if not args.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5.5))
    seen_styles = {}
    series_list = []
    for catalog, session in pairs:
        s = collect_series(dh_hl, catalog, session)
        if s is None:
            print(f"warning: no costed schedules for {session} in {catalog}", file=sys.stderr)
            continue
        series_list.append(s)

    if not series_list:
        sys.exit("error: no plottable data")

    x_end = max(p[0] for s in series_list for p in s["points"])
    x_end += 0.03 * max(x_end, 1.0)  # small right margin so the last plateau shows
    drawn = [plot_series(ax, s, _style_for(s["label"], seen_styles), x_end,
                         show_scatter=args.scatter) for s in series_list]

    ax.axhline(1.0, color="#6b7280", ls="--", lw=1.0, zorder=1, label="reference (cost = 1)")

    # Linear y-axis with optional explicit bounds; steps above ymax (e.g. the
    # initial low-hanging-fruit drop) simply run off the top and are clipped.
    ax.set_xlim(left=min(0.0, min(p[0] for s in series_list for p in s["points"])), right=x_end)
    ax.set_ylim(bottom=args.ymin, top=args.ymax)
    if args.endpoints:
        annotate_endpoints(ax, drawn)
    ax.set_xlabel("seconds since experiment begin")
    ax.set_ylabel("cost  (runtime relative to reference)")
    ax.set_title(args.title)
    ax.grid(True, which="major", axis="both", alpha=0.25)
    ax.legend(fontsize=8, framealpha=0.9)
    fig.tight_layout()

    fig.savefig(args.output, dpi=150)
    print(f"wrote {args.output}")
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
