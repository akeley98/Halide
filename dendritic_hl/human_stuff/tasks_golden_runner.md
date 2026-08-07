# Schedule Node

Remove `runtime error` state.


# Goldens

Golden object

* Timestamp

* ID `golden_{timestamp}`

* Remarks

* Reference to schedule node, or null

Need `json_golden_info` commands and `golden_history` commands.

The most recent golden object's schedule node is the golden schedule node.


# `[schedule ID]` Special Values

Add special schedule ID values `terminus`, `session_output`,
`golden`, and golden ID.


# Problem

List of command line arguments, with special values

* `<RunGenMain>`

* `<Lib>`, also is the `DENDRITIC_HL_OUTPUT_LIB` environment variable

* Any other `<...>` is forbidden

Tri-state: `enabled`, `disabled`, `main` (stored in separate file), mutable

Short name (stored in separate file), mutable

If you don't use `<RunGenMain>` as `argv[0]`,
then the build tool generates a shared library passed by filename as `<Lib>`.

Full ID'd by hash.

Short ID `problem.{short name}` only for enabled problems.


# Cost Model

Private benchmark set objects now need to include cached problem ID.

Cost based on one problem only, by default `main`.

Two-way comparison can be done for multiple problems,
by default all enabled problems.

Output is now list of comparisons, with additional object values

* `problem`: string

* `problem_short_id`: string

Boolean Form:

    {"any_improvement": bool, "any_regression": bool, "any_unknown": bool}

===

Verbose warning for benchmark objects when 0 found:

* Filter by target node

* Filter by second node (anchor, RHS)

* Filter by problem


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

Add `--parameters`.


# Copy Build Output

    dh_hl copy_build_output {what} [parameters_index] [schedule ID]


# ID translation

Commentary full/short ID tool

Benchmark full ID tool (no short ID tool)

WarningToggle full/short ID tool

Problem full/short ID

No short IDs for golden objects, benchmark set objects


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

If there exists a problem in the session open state that is now disabled,
require `--allow-disabled-problems`.

If there exists a golden schedule node in the session open state that is now changed,
require `--allow-changed-golden`.


# Close Session

Takes warning-override flags accepted by `should_accept`.

If `should_accept` for the primary output schedule would give
warning flags, and those flags were not passed, the command fails.
