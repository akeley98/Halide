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
import bisect
import json
import os
import sys
from datetime import datetime, timezone


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
    ("harness_F_guide_T", "harness OFF guide ON",  "#95CC95"),
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
    """Parse a dh_hl timestamp (e.g. '2026-08-13T222943_452308Z'). Naive UTC."""
    return datetime.strptime(text, _TS_FMT)


def _parse_token_log_ts(text):
    """Parse a token_log.jsonl `utc` timestamp -- a DIFFERENT format from dh_hl.

    token_log rows use ISO-8601 with an explicit offset and colons, e.g.
    '2026-08-20T22:34:40.697342+00:00' (not dh_hl's '..._%fZ').  Returns a
    tz-aware UTC datetime.
    """
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _dh_hl_epoch(text):
    """dh_hl timestamp string -> UTC epoch seconds (dh_hl stamps are UTC)."""
    return _parse_ts(text).replace(tzinfo=timezone.utc).timestamp()


# Cumulative token fields carried per timeline row / per schedule.
_TOKEN_FIELDS = ("cum_input", "cum_cache_creation", "cum_cache_read", "cum_output")


def _build_token_lookup(timeline):
    """From a timeline of rows (utc + the _TOKEN_FIELDS) build sorted parallel
    arrays (epochs, {field: [values]}) for step-function lookup by time.  Empty
    timeline -> empty arrays (every lookup then yields 0 for all fields)."""
    rows = sorted(timeline, key=lambda r: _parse_token_log_ts(r["utc"]))
    epochs = [_parse_token_log_ts(r["utc"]).timestamp() for r in rows]
    cols = {f: [r.get(f, 0) for r in rows] for f in _TOKEN_FIELDS}
    return (epochs, cols)


def _tokens_as_of(lookup, epoch):
    """Return {field: cumulative value} as of `epoch`: values at the last timeline
    row with timestamp <= epoch (step function).  All zero before the first row."""
    epochs, cols = lookup
    i = bisect.bisect_right(epochs, epoch) - 1
    if i < 0:
        return {f: 0 for f in _TOKEN_FIELDS}
    return {f: cols[f][i] for f in _TOKEN_FIELDS}


# X-axis modes.  Each is:
#   (filename_suffix, x_of(schedule), x-axis label,
#    end_x_of(experiment), end_marker_label)
# The first reproduces the original seconds chart (end marker = experiment end
# TIME).  The token modes plot cumulative tokens and place the square end marker
# at each experiment's TOTAL tokens (its final token_log row) so the per-
# experiment token price is readable at a glance.
#
# Token accounting with prompt caching: the input the model actually PROCESSED is
# cum_input + cum_cache_creation (cache_creation is freshly-prefilled input
# written to the cache).  cum_cache_read is reused prefix (a cheap reload, not
# recomputed) and is EXCLUDED here -- counting it would re-add the whole context
# on every turn.  Output is never cached.
X_MODES = [
    ("",               lambda s: s.seconds,
     "seconds since experiment began",
     lambda e: e.schedules[-1].seconds,                  "experiment end time"),
    ("_output_tokens", lambda s: s.cum_output,
     "cumulative output tokens",
     lambda e: e.final_cum_output,                       "last token (experiment total)"),
    ("_inout_tokens",  lambda s: s.cum_input + s.cum_cache_creation + s.cum_output,
     "cumulative input + output tokens (cache reads excluded)",
     lambda e: e.final_cum_input + e.final_cum_cache_creation + e.final_cum_output,
     "last token (experiment total)"),
]


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
        # Cumulative token counts as of this schedule's timestamp; filled in by
        # Experiment once the token timeline is known.
        self.cum_input = 0
        self.cum_cache_creation = 0
        self.cum_cache_read = 0
        self.cum_output = 0


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

        # Translate each schedule's timestamp -> cumulative tokens via the
        # experiment's token timeline (empty if no token_log was captured).
        token_lookup = _build_token_lookup(raw.get("token_timeline", []))
        epochs, cols = token_lookup
        self.has_tokens = bool(epochs)
        for s in schedules:
            cum = _tokens_as_of(token_lookup, _dh_hl_epoch(s.timestamp))
            s.cum_input = cum["cum_input"]
            s.cum_cache_creation = cum["cum_cache_creation"]
            s.cum_cache_read = cum["cum_cache_read"]
            s.cum_output = cum["cum_output"]

        # Experiment TOTAL tokens = the last timeline row (the true "token price";
        # this is slightly past the last schedule, since closing turns -- comment,
        # close_session, end_experiment -- also spend tokens).  Used to place the
        # "last token" end marker on the token charts.
        self.final_cum_input = cols["cum_input"][-1] if epochs else 0
        self.final_cum_cache_creation = cols["cum_cache_creation"][-1] if epochs else 0
        self.final_cum_output = cols["cum_output"][-1] if epochs else 0

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

def _draw_experiment(ax, exp, scatter_all, x_right, x_of, end_x):
    """Draw one experiment's curve, scatter, and end marker onto `ax`.

    `x_of(schedule)` gives the x-coordinate (seconds or a cumulative-token count);
    the y-coordinate is always cost.  If `scatter_all` is True every schedule is
    scattered (per-experiment chart); otherwise only "best yet" schedules are
    (top-level chart).  The curve is extended to `x_right` (chart right margin).
    `end_x` is the x of the square end marker (experiment end time on the seconds
    chart; experiment TOTAL tokens on the token charts).
    """
    _pretty, color = _style_for(exp.label)

    # Cost-vs-x curve: f(x) = min cost among schedules with x(schedule) <= x.
    # This is the downward staircase through the "best yet" points.  f stays
    # defined (constant at the final best cost) for all x to the right of the
    # last schedule, so extend the curve all the way to the right margin.
    # (x_of is non-decreasing over time-sorted schedules for every mode, so the
    # staircase stays monotonic in x; token modes may tie several schedules at
    # one x, which shows as a vertical step -- expected.)
    corners = exp.best_yet()
    step_x = [x_of(s) for s in corners] + [x_right]
    step_y = [s.cost for s in corners] + [corners[-1].cost]
    ax.step(step_x, step_y, where="post", color=color, lw=1.8, zorder=3)

    # End marker: square at (end_x, best cost).  On the seconds chart end_x is the
    # newest schedule's time; on the token charts it is the experiment's TOTAL
    # token count, so the square marks the per-experiment token price on the
    # best-cost line.  Drawn *behind* the X/O scatter (but above the curve) and a
    # little larger so its corners peek out when it overlaps a schedule marker.
    ax.scatter([end_x], [exp.best_cost], color=color, marker="s",
               s=90, zorder=3.5, edgecolors="white", linewidths=0.8)

    # Scatter plot: O if clone_in, X otherwise.
    scattered = exp.schedules if scatter_all else corners
    for s in scattered:
        marker = marker_for_schedule(s.clone_in)
        x = x_of(s)
        if marker == "x":
            # 'x' is unfilled and can't take an edgecolor, so give it a white
            # halo -- a thicker white 'x' underneath -- which keeps the X shape
            # legible even when it sits on the same-colored end square.
            ax.scatter([x], [s.cost], color="white", marker=marker,
                       s=42, zorder=3.8, linewidths=3.5)
            ax.scatter([x], [s.cost], color=color, marker=marker,
                       s=42, zorder=4, linewidths=1.5)
        else:
            # 'o' gets a thin white edge so light colors stay visible.
            ax.scatter([x], [s.cost], color=color, marker=marker,
                       s=42, zorder=4, edgecolors="white", linewidths=0.8)


def _x_bounds(experiments, x_of, end_x_of):
    """Return (left, right) x-limits that fit every plotted scatter point AND
    every end marker (the token-total marker can sit past the last schedule)."""
    xs = [x_of(s) for exp in experiments for s in exp.schedules]
    xs += [end_x_of(exp) for exp in experiments]
    x_lo, x_hi = min(xs), max(xs)
    margin = 0.03 * max(x_hi - x_lo, 1.0)
    return (min(0.0, x_lo) - margin, x_hi + margin)


def _finish_axes(ax, x_bounds, title, ymin, ymax, xlabel):
    """Apply the shared cost=1 line, bounds, labels, and grid."""
    # Black dashed reference line at cost = 1.
    ax.axhline(1.0, color="black", ls="--", lw=1.0, zorder=1)

    ax.set_xlim(left=x_bounds[0], right=x_bounds[1])
    # Y bounds come straight from the CLI.
    ax.set_ylim(bottom=ymin, top=ymax)

    ax.set_xlabel(xlabel)
    ax.set_ylabel("lowest cost  (runtime relative to reference)")
    ax.set_title(title)
    ax.grid(True, which="major", axis="both", alpha=0.25)


def _make_legend(ax, plt, labels_in_order, end_marker_label):
    """Add a legend: colored-line label entries, then the marker key entries.

    `end_marker_label` is the text for the square end-marker key (e.g.
    "experiment end time" or "last token (experiment total)").
    """
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
    handles.append(Line2D([0], [0], color="black", marker="s",
                          linestyle="None", markersize=8,
                          markerfacecolor="black"))
    texts.append(end_marker_label)

    ax.legend(handles, texts, fontsize=8, framealpha=0.9)


def render_top_chart(plt, experiments, out_path, title, ymin, ymax, x_bounds,
                     x_of, xlabel, end_x_of, end_marker_label):
    """Top-level chart: every experiment, best-yet scatter only."""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for exp in experiments:
        _draw_experiment(ax, exp, scatter_all=False, x_right=x_bounds[1],
                         x_of=x_of, end_x=end_x_of(exp))
    _finish_axes(ax, x_bounds, title, ymin, ymax, xlabel)
    # Legend lists every configured label, in configuration order.
    _make_legend(ax, plt, [label for label, _, _ in LABEL_STYLE], end_marker_label)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def render_experiment_chart(plt, exp, out_path, title, ymin, ymax, x_bounds,
                            x_of, xlabel, end_x_of, end_marker_label):
    """Per-experiment chart: one experiment, all schedules scattered.

    Uses the same `x_bounds` as the top-level chart so the charts share an
    x-axis and can be flipped through without the axis shifting.
    """
    fig, ax = plt.subplots(figsize=(9, 5.5))
    _draw_experiment(ax, exp, scatter_all=True, x_right=x_bounds[1],
                     x_of=x_of, end_x=end_x_of(exp))
    _finish_axes(ax, x_bounds, title, ymin, ymax, xlabel)
    _make_legend(ax, plt, [exp.label], end_marker_label)
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

    # For each x-axis mode (seconds, output tokens, input+output tokens) render
    # a top-level chart and one per-experiment chart.  The token charts get the
    # filename suffix (e.g. chart_output_tokens.png) and omit the end marker.
    # X bounds are computed once per mode over all experiments and shared by every
    # chart of that mode, so the axes line up when flipping through them.
    for suffix, x_of, xlabel, end_x_of, end_marker_label in X_MODES:
        x_bounds = _x_bounds(experiments, x_of, end_x_of)

        render_top_chart(
            plt, experiments,
            os.path.join(args.output, "chart" + suffix + ".png"),
            args.title, args.ymin, args.ymax, x_bounds,
            x_of, xlabel, end_x_of, end_marker_label)

        for exp in experiments:
            # Per-experiment chart lives at the top level (next to chart.png) so
            # it is easy to flip through in eog -- deliberately *not* inside the
            # per-experiment directory.
            render_experiment_chart(
                plt, exp,
                os.path.join(args.output, exp.dir_name + suffix + ".png"),
                args.title, args.ymin, args.ymax, x_bounds,
                x_of, xlabel, end_x_of, end_marker_label)

    # Per-experiment source/parameter/cost outputs (written once, not per mode).
    for exp in experiments:
        write_experiment_dir(exp, os.path.join(args.output, exp.dir_name))

    # Flag experiments that lack token data (their token charts will be flat at
    # x=0 -- see task note about insufficient logging).
    no_tokens = [e.dir_name for e in experiments if not e.has_tokens]
    if no_tokens:
        sys.stderr.write("WARNING: no token timeline for: {}\n".format(
            ", ".join(no_tokens)))

    print("wrote {} ({} experiments, {} x-modes)".format(
        args.output, len(experiments), len(X_MODES)))


if __name__ == "__main__":
    main()
