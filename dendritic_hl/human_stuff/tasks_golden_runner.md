# Schedule Node

Remove `runtime error` state.


# Goldens

Golden object

* Timestamp

* ID `golden_{timestamp}`

* Commentary

* Reference to schedule node, or null

Need `json_golden_info` commands and `golden_history` commands.

The most recent golden object's schedule node is the golden schedule node.


# `[schedule ID]` Special Values

Add special schedule ID values `terminus`, `seed`, `session_output`,
`golden`, and golden ID.


# Problem

List of command line arguments, with special values

* `<RunGenMain>`

* `<Lib>`, also is the `DENDRITIC_HL_OUTPUT_LIB` environment variable

* Any other `<...>` is forbidden

Each can be enabled or disabled (stored in separate file).

If you don't use `<RunGenMain>` as `argv[0]`,
then the build tool generates a shared library passed by filename as `<Lib>`.

ID'd by hash.

NB don't know yet how to deal with cost model stuff with multiple problems.


# Build

Build to shared library and/or RunGenMain.

Save algorithm `.hlpipe` (handshakes with generator),
`DENDRITIC_HL_ALGORITHM_HLPIPE` environment variable.

Profile allows selection of problems.

When profiling, find the expected pipeline by generator by `name`
field in profiler JSON output; should have correct hash.


# Benchmark

Get rid of old short IDs.
Short IDs are now local to sessions, stored in private state:

    private.{schedule ID}.{parameters_index}.{serial}
    private/benchmark_short_id/{schedule ID}/{parameters index}.json

New key/value pairs

* `parameters_index`: number

* `problem`: string


# Copy Schedule, ID-of Schedule Tools

Remove terminus/seed/session variants,
replaced by `[schedule ID]` default behavior.

Add `--pick`,

`generator`: default, generator C++

`parameters`


# Copy Build Output

    dh_hl copy_build_output {what} [parameters_index] [schedule ID]

`generator`, `header`, `shared_library`, `algorithm_hlpipe`


# ID translation

Commentary full/short ID tool

Benchmark full ID tool (no short ID tool)

WarningToggle full/short ID tool


# Session Open

Public state:
golden schedule node,
list of enabled problems.


# Should Accept

    dh_hl should_accept -s ... [schedule ID]

**All sessions:**

Look for reachable benchmarks for the schedule.
For each enabled problem and each generator parameters, look for a benchmark.
If none found, require `--allow-failed-problems`.

**Top-level sessions only:**

If there is a golden schedule node, check binary equality of
its `algorithm_hlpipe` compared to the given schedule node's.
If `algorithm_hlpipe` not found, give warnings.
If they differ, require `--allow-failed-golden`.


# Close Session

Takes warning-override flags accepted by `should_accept`.

If `should_accept` for the primary output schedule 
