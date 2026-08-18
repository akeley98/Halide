# Plot Rendering

I need a new plotting tool `plot_render.py`
to replace the `old_plot.py` in this directory.
You may reference the old code if recycling stuff is useful.
The old tool bundled together gathering data and plotting data.
The new tool with get the data pre-gathered as a single JSON list.
The new tool will also output a directory full of results,
rather than just a single PNG chart (which drops lots of information).


# Command Line Arguments

    --input {input.json} --output {output dir name} --ymin {number} --ymax {number} --title {title}

Default `0.8` for `ymin`, `2.0` for ymax, `Best schedule cost over time` for `title`.

Input and output are mandatory.

No need to re-implement the extra flexibility in `old_plot.py`.


# Input Object Schema

See `plot_generate_data.py`


# Output Directory Contents

* `chart.png`: top-level chart.
  All experiments plotted (according to output A format).

* `{label}_{n}.png`: per-experiment chart (only one experiment plotted).
  Each experiment is named by its label and a serial number `{n}`.
  The serial number is 0 for the oldest (lowest `begin_timestamp`)
  experiment of that label, 1 for the second-oldest, etc.

* `{label}_{n}/`, per-experiment schedules (see output B).

* `{label}_{n}/cost_list.json`, per-experiment cost list (see output C).

NB inconsistency of per-experiment chart not being in the
per-experiment directory is intentional.
This is to make flipping through the charts in `eog` easier.


# Output A: Charts

X-axis: "seconds since experiment began"

Y-axis: "cost  (runtime relative to reference)"

The "runtime relative to reference" has already been computed
as the costs in the input JSON.


## Legend Format

The top-level chart legend starts with a list of
(colored line, pretty-printed label) pairs, in order

* `harness_F_guide_F`: "harness OFF guide OFF" (color: `#BCBCBC`)
* `harness_F_guide_T`: "harness OFF guide ON" (color: `#AAE8AA`)
* `harness_T_guide_F`: "harness ON guide OFF" (color: `#333333`)
* `harness_T_guide_T`: "harness ON guide ON" (color: `#009500`)

The per-experiment plot has only the line legend entry
needed for that experiment's label.

For all charts, have a black X as `clone_in found: NO`
and black O as `clone_in found: YES`.
Finally have a black square as `experiment end time`.

All chart elements drawn (curves, etc.) for a specific experiment
are colored according to the legend.


## Cost=1 Line

Draw a black dashed line `cost = 1`.


## Scatter Plot

Each schedule may be plotted as a point,
using an O shape if the sub-string "clone_in" is found in the C++ source,
and an X shape otherwise.
Parse the timestamps to figure out the X coordinate.

For the per-experiment chart, all schedules for that experiment are plotted.

For the top-level chart, only "best yet" schedules are plotted.
These are schedules whose cost is strictly lower than that of all previous
schedules in that experiment.


## Cost vs. Time Curve

For each plotted experiment, plot

    f(x) = min(sch.y for sch in scatter_plot_points if sch.x <= x)

Draw no curve at locations where this is undefined
(i.e. the `x` is lower than that of all scatter plot points).

This is effectively a "downwards staircase" connecting the
"best yet" scatter plot points, extended rightwards.


## Experiment End Marker

For each plotted experiment, plot a square at (x, y) location

    ("seconds since experiment began" of newest schedule,
      cost of best schedule)

This puts a stop marker at a location where it'll visually be
associated with the corresponding experiment's curve.


## X/Y Bounds

X bounds should be such that all scatter plot points fit
(NB this varies for the per-experiment charts).

Y bounds are as specified by the CLI.


# Output B: Per-Experiment Schedules

Name each schedule in the experiment as `{seconds}_{n}` where
`{seconds}` is the rounded-to-int number of seconds since the
experiment began, and `{n}` is a disambiguation number 0, 1, 2... in
case of `{seconds}` collision (not sure if this is ever needed).

In the per-experiment directory, for each schedule, output

* `{seconds}_{n}_generator.cpp`, generator C++ source

* `{seconds}_{n}_parameters.json`, parameters JSON object


# Output C: Per-Experiment Cost List

List of objects; one object for each schedule in the experiment,
with key/value pairs:

* `name`: string (`{seconds}_{n}`)

* `cost`: number

* `clone_in`: bool (`"clone_in"` substring in C++ source)

Sorted lexicographically by `(seconds, n)`.

Print each object in the list on exactly one line,
and align the `name`/`cost`/`clone_in` data as "columns",
so the object doubles as a human-readable table.


# Likely Future Changes (Write for Upgradability)

* May want to illustrate source code features other than `clone_in`.

* `label` scheme may change.
  There may be more or fewer than 4 labels in the future,
  each with a custom legend entry and color scheme.


# Testing

This is a throwaway script for an experiment,
so formal `pytest` testing is not expected.
You may test with the gitignored `../sandbox` directory.
It contains `../sandbox/plot_data.json` which is my real experiment data
(backed up elsewhere).
You can also cook your own fake data for testing.
