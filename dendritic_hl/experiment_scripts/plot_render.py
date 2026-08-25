#!/usr/bin/env python3
"""Render an experiment plot directory from pre-gathered JSON data.

This replaces old_plot.py.  Unlike old_plot.py, this tool does *not* gather
data itself: it consumes the single JSON list produced by
plot_generate_data.py (see that file for the input object schema).  Instead of
emitting a single PNG (which throws away most of the information), it writes a
whole output directory: a top-level chart, a per-experiment chart for each
experiment, and per-experiment sub-directories holding the raw schedule sources,
parameters, and a human-readable cost list.

Usage:
    plot_render.py --input DATA.json --output OUTDIR
                   [--ymin 0.8] [--ymax 2.0] [--title TITLE]

Input and output are mandatory.
"""

import argparse
import json
import os
import sys
from datetime import datetime


# --------------------------------------------------------------------------- #
# Configuration (written to be easy to edit -- see "Likely Future Changes")
# --------------------------------------------------------------------------- #

# dendritic_hl timestamp format, e.g. "2026-08-13T222943_452308Z".
_TS_FMT = "%Y-%m-%dT%H%M%S_%fZ"

# One entry per experiment label, in the order they should appear in the
# top-level chart legend.  Each maps a raw label to its pretty legend text and
# the color used for *all* chart elements (curve, scatter, end marker) of any
# experiment with that label.  There may be more or fewer than four labels in
# the future; add/remove/re-color entries here.
LABEL_STYLE = [
    ("harness_F_guide_F", "harness OFF guide OFF", "#BCBCBC"),
    ("harness_F_guide_T", "harness OFF guide ON",  "#AAE8AA"),
    ("harness_T_guide_F", "harness ON guide OFF",  "#333333"),
    ("harness_T_guide_T", "harness ON guide ON",   "#009500"),
]
_STYLE_BY_LABEL = {label: (pretty, color) for label, pretty, color in LABEL_STYLE}
# Fallback color for a label not listed above (keeps the tool from crashing if
# the label scheme changes before this table is updated).
_FALLBACK_COLOR = "#CC00CC"


def _style_for(label):
    """Return (pretty_label, color) for an experiment label."""
    if label in _STYLE_BY_LABEL:
        return _STYLE_BY_LABEL[label]
    return (label, _FALLBACK_COLOR)


# Source-feature detection.  Today we only distinguish whether the schedule uses
# clone_in; in the future we may want to illustrate other source features, so
# marker selection is funnelled through these two helpers.
def source_has_clone_in(source):
    """True iff the schedule's C++ source uses clone_in."""
    return "clone_in" in source


def marker_for_schedule(clone_in):
    """Scatter marker for a schedule: O if clone_in is present, else X."""
    return "o" if clone_in else "x"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

def _parse_ts(text):
    return datetime.strptime(text, _TS_FMT)


class Schedule:
    """One schedule within an experiment, with derived plotting fields."""

    def __init__(self, raw, begin):
        self.source = raw["source"]
        self.parameters = raw["parameters"]
        self.cost = float(raw["cost"])
        self.timestamp = raw["timestamp"]
        # Fractional seconds since the experiment began (used for plotting).
        # This can be slightly negative if a schedule predates the recorded
        # begin timestamp.
        self.seconds = (_parse_ts(self.timestamp) - begin).total_seconds()
        # Rounded-to-int seconds (used for naming).
        self.int_seconds = int(round(self.seconds))
        self.clone_in = source_has_clone_in(self.source)
        # Filled in once the whole experiment is known.
        self.name = None        # "{int_seconds}_{n}"
        self.is_best_yet = False


class Experiment:
    """One experiment: a labelled, time-ordered list of schedules."""

    def __init__(self, raw, serial):
        self.label = raw["label"]
        self.serial = serial            # 0 = oldest experiment of this label
        self.begin_timestamp = raw["begin_timestamp"]
        begin = _parse_ts(self.begin_timestamp)

        schedules = [Schedule(s, begin) for s in raw["schedules"]]
        schedules.sort(key=lambda s: s.timestamp)
        self.schedules = schedules

        # Assign "{seconds}_{n}" names: n disambiguates int_seconds collisions,
        # counted in time order.
        seen_seconds = {}
        for s in schedules:
            n = seen_seconds.get(s.int_seconds, 0)
            seen_seconds[s.int_seconds] = n + 1
            s.name = "{}_{}".format(s.int_seconds, n)

        # Mark strict "best yet" improvements (cost strictly below all earlier).
        best = float("inf")
        for s in schedules:
            if s.cost < best:
                best = s.cost
                s.is_best_yet = True

    @property
    def dir_name(self):
        return "{}_{}".format(self.label, self.serial)

    @property
    def best_cost(self):
        return min(s.cost for s in self.schedules)

    def best_schedule(self):
        """The lowest-cost schedule (earliest one wins ties, arbitrarily)."""
        return min(self.schedules, key=lambda s: s.cost)

    @property
    def end_seconds(self):
        # Newest schedule (schedules are time-sorted).
        return self.schedules[-1].seconds

    def best_yet(self):
        return [s for s in self.schedules if s.is_best_yet]


def load_experiments(path):
    """Load the input JSON and build Experiment objects, dropping empties.

    Experiments are ordered by begin timestamp, and each is given a per-label
    serial number (0 for the oldest experiment of that label, etc.).
    """
    raw_list = json.load(open(path))
    raw_list = [r for r in raw_list if r["schedules"]]
    raw_list.sort(key=lambda r: r["begin_timestamp"])

    label_counts = {}
    experiments = []
    for raw in raw_list:
        serial = label_counts.get(raw["label"], 0)
        label_counts[raw["label"]] = serial + 1
        experiments.append(Experiment(raw, serial))
    return experiments


# --------------------------------------------------------------------------- #
# Charts (Output A)
# --------------------------------------------------------------------------- #

def _draw_experiment(ax, exp, scatter_all, x_right):
    """Draw one experiment's curve, scatter, and end marker onto `ax`.

    If `scatter_all` is True every schedule is scattered (per-experiment chart);
    otherwise only "best yet" schedules are (top-level chart).  The curve is
    extended out to `x_right` (the chart's right margin).
    """
    _pretty, color = _style_for(exp.label)

    # Cost-vs-time curve: f(x) = min cost among schedules with seconds <= x.
    # This is the downward staircase through the "best yet" points.  f stays
    # defined (constant at the final best cost) for all x to the right of the
    # last schedule, so extend the curve all the way to the right margin.
    corners = exp.best_yet()
    step_x = [s.seconds for s in corners] + [x_right]
    step_y = [s.cost for s in corners] + [corners[-1].cost]
    ax.step(step_x, step_y, where="post", color=color, lw=1.8, zorder=3)

    # Scatter plot: O if clone_in, X otherwise.
    # Experiment end marker: square at (newest schedule seconds, best cost).
    # Drawn *behind* the X/O scatter (but above the curve) and made a little
    # larger so its corners peek out: when the final schedule has ~the same
    # cost as the best and the two markers overlap, the X/O on top still shows
    # what kind of schedule the last one was.
    ax.scatter([exp.end_seconds], [exp.best_cost], color=color, marker="s",
               s=90, zorder=3.5, edgecolors="white", linewidths=0.8)

    scattered = exp.schedules if scatter_all else corners
    for s in scattered:
        marker = marker_for_schedule(s.clone_in)
        if marker == "x":
            # 'x' is unfilled and can't take an edgecolor, so give it a white
            # halo -- a thicker white 'x' underneath -- which keeps the X shape
            # legible even when it sits on the same-colored end square.
            ax.scatter([s.seconds], [s.cost], color="white", marker=marker,
                       s=42, zorder=3.8, linewidths=3.5)
            ax.scatter([s.seconds], [s.cost], color=color, marker=marker,
                       s=42, zorder=4, linewidths=1.5)
        else:
            # 'o' gets a thin white edge so light colors stay visible.
            ax.scatter([s.seconds], [s.cost], color=color, marker=marker,
                       s=42, zorder=4, edgecolors="white", linewidths=0.8)


def _x_bounds(experiments):
    """Return (left, right) x-limits that fit every plotted scatter point."""
    xs = [s.seconds for exp in experiments for s in exp.schedules]
    x_lo, x_hi = min(xs), max(xs)
    margin = 0.03 * max(x_hi - x_lo, 1.0)
    return (min(0.0, x_lo) - margin, x_hi + margin)


def _finish_axes(ax, x_bounds, title, ymin, ymax):
    """Apply the shared cost=1 line, bounds, labels, and grid."""
    # Black dashed reference line at cost = 1.
    ax.axhline(1.0, color="black", ls="--", lw=1.0, zorder=1)

    ax.set_xlim(left=x_bounds[0], right=x_bounds[1])
    # Y bounds come straight from the CLI.
    ax.set_ylim(bottom=ymin, top=ymax)

    ax.set_xlabel("seconds since experiment began")
    ax.set_ylabel("lowest cost  (runtime relative to reference)")
    ax.set_title(title)
    ax.grid(True, which="major", axis="both", alpha=0.25)


def _make_legend(ax, plt, labels_in_order):
    """Add a legend: colored-line label entries, then the marker key entries."""
    from matplotlib.lines import Line2D

    handles, texts = [], []
    for label in labels_in_order:
        pretty, color = _style_for(label)
        handles.append(Line2D([0], [0], color=color, lw=2.5))
        texts.append(pretty)

    # Marker key (drawn in black; real markers are colored per experiment).
    handles.append(Line2D([0], [0], color="black", marker="x", linestyle="None",
                          markersize=8))
    texts.append("clone_in found: NO")
    handles.append(Line2D([0], [0], color="black", marker="o", linestyle="None",
                          markersize=8, markerfacecolor="black"))
    texts.append("clone_in found: YES")
    handles.append(Line2D([0], [0], color="black", marker="s", linestyle="None",
                          markersize=8, markerfacecolor="black"))
    texts.append("experiment end time")

    ax.legend(handles, texts, fontsize=8, framealpha=0.9)


def render_top_chart(plt, experiments, out_path, title, ymin, ymax, x_bounds):
    """Top-level chart: every experiment, best-yet scatter only."""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for exp in experiments:
        _draw_experiment(ax, exp, scatter_all=False, x_right=x_bounds[1])
    _finish_axes(ax, x_bounds, title, ymin, ymax)
    # Legend lists every configured label, in configuration order.
    _make_legend(ax, plt, [label for label, _, _ in LABEL_STYLE])
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def render_experiment_chart(plt, exp, out_path, title, ymin, ymax, x_bounds):
    """Per-experiment chart: one experiment, all schedules scattered.

    Uses the same `x_bounds` as the top-level chart so the charts share an
    x-axis and can be flipped through without the axis shifting.
    """
    fig, ax = plt.subplots(figsize=(9, 5.5))
    _draw_experiment(ax, exp, scatter_all=True, x_right=x_bounds[1])
    _finish_axes(ax, x_bounds, title, ymin, ymax)
    _make_legend(ax, plt, [exp.label])
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Per-experiment schedules & cost list (Output B & C)
# --------------------------------------------------------------------------- #

def write_experiment_dir(exp, exp_dir):
    """Write per-schedule sources/parameters (B) and the cost list (C)."""
    os.makedirs(exp_dir, exist_ok=True)

    # Output B: one generator.cpp and one parameters.json per schedule.
    for s in exp.schedules:
        with open(os.path.join(exp_dir, s.name + "_generator.cpp"), "w") as f:
            f.write(s.source)
        with open(os.path.join(exp_dir, s.name + "_parameters.json"), "w") as f:
            json.dump(s.parameters, f, indent=2)

    # A copy of the lowest-cost schedule, for convenient reference.
    best = exp.best_schedule()
    with open(os.path.join(exp_dir, "best_generator.cpp"), "w") as f:
        f.write(best.source)
    with open(os.path.join(exp_dir, "best_parameters.json"), "w") as f:
        json.dump(best.parameters, f, indent=2)

    # Output C: cost_list.json.
    write_cost_list(exp, os.path.join(exp_dir, "cost_list.json"))


def write_cost_list(exp, path):
    """Write cost_list.json: one aligned object per line, doubling as a table.

    Sorted lexicographically by (int_seconds, n).
    """
    rows = sorted(exp.schedules, key=lambda s: (s.int_seconds,
                                                int(s.name.rsplit("_", 1)[1])))

    # Pre-render each field as it will appear in the JSON, then pad to align.
    cells = []
    for s in rows:
        cells.append((
            json.dumps(s.name),                      # "12_0"
            repr(s.cost),                            # exact round-trippable float
            "true" if s.clone_in else "false",
        ))
    name_w = max((len(c[0]) for c in cells), default=0)
    cost_w = max((len(c[1]) for c in cells), default=0)
    clone_w = max((len(c[2]) for c in cells), default=0)

    lines = []
    for name, cost, clone in cells:
        lines.append('  {{"name": {}, "cost": {}, "clone_in": {}}}'.format(
            name.ljust(name_w), cost.ljust(cost_w), clone.ljust(clone_w)))

    with open(path, "w") as f:
        if lines:
            f.write("[\n" + ",\n".join(lines) + "\n]\n")
        else:
            f.write("[]\n")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, metavar="JSON",
                    help="input JSON list of experiment objects")
    ap.add_argument("--output", required=True, metavar="DIR",
                    help="output directory to create/populate")
    ap.add_argument("--ymin", type=float, default=0.8,
                    help="minimum cost on the y-axis (default 0.8)")
    ap.add_argument("--ymax", type=float, default=2.0,
                    help="maximum cost on the y-axis (default 2.0)")
    ap.add_argument("--title", default="Best schedule cost over time",
                    help="chart title")
    args = ap.parse_args(argv)

    experiments = load_experiments(args.input)
    if not experiments:
        sys.exit("error: no experiments with schedules in {}".format(args.input))

    os.makedirs(args.output, exist_ok=True)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # X bounds are computed once over all experiments and shared by every
    # chart, so the axes line up when flipping through them.
    x_bounds = _x_bounds(experiments)

    # Top-level chart.
    render_top_chart(plt, experiments, os.path.join(args.output, "chart.png"),
                     args.title, args.ymin, args.ymax, x_bounds)

    # Per-experiment outputs.
    for exp in experiments:
        # Per-experiment chart lives at the top level (next to chart.png) so it
        # is easy to flip through in eog -- deliberately *not* inside the
        # per-experiment directory.
        render_experiment_chart(
            plt, exp, os.path.join(args.output, exp.dir_name + ".png"),
            args.title, args.ymin, args.ymax, x_bounds)
        write_experiment_dir(exp, os.path.join(args.output, exp.dir_name))

    print("wrote {} ({} experiments)".format(args.output, len(experiments)))


if __name__ == "__main__":
    main()
