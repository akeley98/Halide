I need some matplotlib charts created by a new script `../experiment_scripts/plot.py`.
This will be reading some performance numbers from a `dendritic_hl` catalog,
accessed through the `dh_hl` command that is on the `PATH`,
with env var `DENDRITIC_HL_ALLOW_HARNESS=1`.

Install stuff to `../.venv` if needed.

This is a throwaway script, and does not need to be tested to the same
standard as `dh_hl` itself.

The script will be given a catalog path and a session ID.
The script will plot the performance of the best schedule found
as a function of time.

Using `dh_hl`, inspect the catalog's

* begin timestamp: `dh_hl experiment -C ... get_begin_timestamp`

* list of schedule nodes: `dh_hl experiment -C ... json_test_schedules`

Find the cost and timestamp of each schedule node using
`dh_hl json_ranking_cost -s ...`, `dh_hl json_schedule_info`.

The horizontal axis is "seconds since the begin timestamp", linear,
and the vertical axis is "cost", logarithmic.
Draw a horizontal line `cost = 1`
(the cost is the runtime relative to a reference schedule).

The graphed function is

    f(t) = min(node.cost for node in json_test_schedules if node.timestamp <= t)

Note the graph should look like a series of horizontal and
vertical lines forming "steps".
Just draw nothing where the value is undefined
(due to zero schedules matching the criterion).

Smoke test using the catalog and session named in
`/Users/dakeley/local_laplacian_charts/sessions.json`
(don't worry about breaking the catalog;
the experiment data is backed up on Github).

Write for future extensibility:

* Will later plot multiple lines on the same chart,
  each corresponding to a different profiler session.
  The input will be a list of (catalog path, session ID) pairs.

* Will color and symbol code (o, x, etc.) based on the experiment label.
