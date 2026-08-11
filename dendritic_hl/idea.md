# Dendritic Halide Harness — Harness for agentic Halide scheduling

The process of scheduling Halide code — whether by hand or automated — is often a sort of tree search.
Schedules evolve over time into other schedules, and some plans don't work out and get back-tracked.
This harness makes this process explicit as a "catalog" of schedules, organized into a historic tree structure.

The goals of the Dendritic Halide Harness (`dh_hl`) are:

* **Long Term Memory Device:** Give agents a system for long-term
  coordination and progress tracking.
  Include structured commentary and suggested ideas for each schedule.
  Also, form the basis for building a future UI for humans (or planner
  agents) to oversee the scheduling process.

* **Benchmarking Hygiene:** Automatically ensure each benchmark/profiling
  result gets attached IMMEDIATELY to the C++ source code used,
  also annotated with system information.
  Corollary: all C++ source code ever compiled will be catalogued,
  allowing us to monitor stats on how often agents generate illegal schedules.
  Make life easier for now by assuming one C++ file containing a typical
  Halide::Generator setup (2-phase build, C++ -> C++ bin -> Halide bin).
  **Simplifying assumption:** the workspace C++ file registers **exactly one**
  generator. This lets the build/profile tools discover the generator name
  automatically (see the Build Tool) instead of parsing it out of the source.

* **Rich Cost Comparison Tools:** Built-in tools for comparing
  different schedules based on real runtime, with automatic
  avoidance of statistical and noise pitfalls.

* **Maximize Compatibility with Source Control:**
  "transparent"-ish on-disk state for the catalog,
  designed to minimize merge conflicts.
  This is also why it's "catalog" and not "repository".

* **Support for Parallel Agent Sessions:**
  Each agent gets an exclusive **session node** and private workspace,
  which the agent provides as a handle to every `dh_hl` harness command.
  The session nodes are catalogued as another tree structure,
  with successor and sub-agent sessions.
  A session is *opened* with a seed idea and *closed* with an output schedule.
  The catalog data structure on-disk is robust to multiple concurrent agent sessions,
  and implements a machine-wide lockout that prevents benchmarking from
  competing with other harness usage for CPU time.

* Implemented as a **Python 3 package** for now, launched with the `dh_hl` stub.

AGENTS: if you are **implementing** this harness,
see the companion [Implementation Notes](impl.md).


# Conceptual State

The catalog is stored in a directory whose name ends with `.dh_hl`.

The catalog primarily consists of a bipartite tree consisting of:

* **Schedule Nodes:** Holds C++ generator file and generator parameters.
  May have 0 or 1 "idea nodes" as parents.
  May have 0 or more idea nodes as children as well only if this is a major schedule (to be defined).
  The schedule is embedded with a UTC wall time timestamp.
  The schedule may have commentary, benchmark, or `WarningToggle` sub-objects attached.

* **Idea Nodes:** Holds a reference to exactly 1 parent schedule node,
  and includes a text proposal of how to further modify the schedule.
  The child schedule nodes are attempts of implementing the idea.
  Up to one of the child schedules is the idea node's **canonical schedule**.

Furthermore, there is a side tree of **Session Nodes**
representing the progress and workspace of a single agent.
This tree contains pointers to the primary schedule/idea tree.
Multiple agents can work on the same catalog in parallel,
but each must have its own session.

For correctness testing, the catalog maintains **problem objects**
giving command line args for a "runner" process, and **golden objects**,
which reference a schedule node giving the expected Halide *algorithm*.

Finally, the `build` (profiler) tool creates **benchmark set objects**
that group "batches" of benchmarks across different schedule nodes.
This is for comparison tools, which only compare within batches to fight noise.


### Schedule Node Terminology

A schedule node is a **root node** if it has 0 parents.
The "tree" is technically a forest as multiple roots are possible.

A schedule node is a **major schedule** if it is a root node or it is
a canonical schedule of some idea node.

A schedule node is a **minor schedule** if it's not a major schedule.
The point of this is mainly to track "failed or flawed attempts" to implement the idea.
The "core state" of the catalog is the network of major schedules.
Minor schedules should not store interesting variations on schedules;
this should be done by adding a legitimate child idea node.


### Tree Structure Invariants

* The parent of an idea node must be a **major schedule**.

* The parent of an idea node must be older than (have strictly
  lower timestamp than) each child schedule node of the idea.

* The parent of a given session node must be older than the given session node.

Generally, tools only check that new edges they create satisfy the invariants,
and they don't diagnose pre-existing violations.
**Exception:** tools check invariants as-needed to guard against infinite loops.

*History note:* idea nodes used to have timestamps as well,
and this made the timestamp invariant easier, but made
`fix_canonical` and `force_parent_idea` impossible.


### Timestamp Format

UTC time, formatted with `strftime("%Y-%m-%dT%H%M%S_%fZ")` (microsecond precision).
These are sortable lexicographically, assuming the year is between 1000 and 9999 CE.
We assume these increase monotonically even though leap seconds screw us.
(Note, we avoid `.` due to short IDs, to be described later).


### Hash Format

sha256, lowercase hex digits.


## Schedule Node State

* **C++ source code:** `generator.cpp`

* **Generator Parameters:** `generator_parameters.json`:
  list of generator parameter JSON objects.
  Each leads to a different Halide binary to be generated and benchmarked.

* **UTC wall time timestamp:** timestamp of when the schedule node was created.

* **Schedule Node Full ID:** `{timestamp}_{hash}`; exactly 90 characters.

* **Edges:** 0 or 1 parent idea nodes, 0 or more child idea nodes.

* **Result:** one of:
  * `unknown`: Did not attempt any compilation (worst).
  * `c++ error`: C++ generator did not compile successfully.
  * `halide error`: C++ generator compiled, but not known *yet*
    that all generator parameters lead to a valid Halide binary.
  * `success`: All Halide binaries built successfully (best).

* **Benchmark Sub-objects:** JSON format, documented later.

* **Commentary Sub-objects:**
  remarks with possible opinionated review; documented later.

* **Review:** derived value from commentary; documented later.

* **Warning Toggles:** list of `WarningToggle` sub-objects, documented later.
  These are used to suppress warnings from the profiler.


## Generator Parameters JSON Object Format

Object mapping generator parameter names to values.
Each value can be bool, number, or string.
All pairs go to the Halide generator as `key=value`.


## Idea Node State

* **Proposal Name:** String of length in `[1, 72]`, containing only alphanumeric characters and underscore.

* **Edges:** Exactly one parent schedule node, any number of child schedule nodes.

* **Idea Node Full ID:** `{proposal name}_{parent id}`; since the `parent id` is fixed-width, the proposal name can be derived easily.

* **Proposal Text**

* **Canonical Schedule:** Either nothing, or one of those child schedule nodes, referenced by ID.
  For idea nodes that are not seeding a session,
  this is intended to be the schedule that implements the proposal text to the agent's satisfaction.
  The other child schedules are compiler errors or imperfect attempts,
  tracked for research purposes.

* **Review:** Inherits the review value of the canonical schedule.
  The review is `neutral` if there's no canonical schedule.

* **Idea Side Links:** Encodes semantic connections between ideas,
  outside the tree discipline.
  A link is directional: it connects one idea node to another,
  and is either a `borrows_from` link, or a `superseded_by` link.


## Session Node State

* **Session Node Full ID:** `{depth}_{timestamp}_{username}@{hostname}`.
  This is, for now, intentionally de-anonymizing.
  The `username` and `hostname` are sanitized in the filename.
  The `hostname` is the *stable* hostname (see impl.md "Stable Hostname"),
  which on a Mac may contain spaces/punctuation before sanitization.

* **Prompt:** Plain text.

* **Seed Ideas:** References to ideas to start at.

* **Is-delisted Flag:** Initially false.

* **Depth** (int); top-level sessions have 0 depth.

* **Parent Session:** Optional, reference to another session node.

* **Default Anchor Schedule:** Optional reference to schedule node;
  see "Cost Comparison Methodology".

* **Golden Schedule Node on Opening:**
  Optional reference to schedule node;
  this is the golden schedule node at the time the session was created.

* **Enabled Problems on Opening:**
  Saved list of the enabled problem objects that existed when the session was created.

* **Outputs:** Optional, added when a session is closed.
  This is the "final result" of the session.
  It consists of an ordered list of output schedule nodes
  (each mapped to a string "pool tag")
  and output benchmark sets.
  Output schedule commentary should be used to summarize the session findings.
  The first output schedule is the "primary output schedule"
  which usually should be the best found.

* **Session Private Workspace** state: gitignore'd per-session-node state.
  This contains a session lock,
  current idea state,
  current anchor schedule,
  a session private idea list,
  a session private benchmark set list,
  workspace files (`generator.cpp` and `generator_parameters.json`),
  and a `bin` directory.

Most harness tools require a "current session",
which is identified with the catalog directory
and the full ID of a session node within the catalog.
The pair can be succinctly communicated using "session handles",
described a few sections later.

**Session Top-Priority Rule:**
two concurrent agents must never have the same
current session, unless the tool is marked as an exception
(`does not acquire session lock`).
The session lock (see "Locking") will catch many such violations,
but will not prevent observing a partial edit to the workspace C++ file.


## Commentary Sub-object State

* **Text of commentary**

* **Commentary Full ID:**
  `{parent schedule full ID}_{timestamp}_{hash}`
  where `hash` is that of the commentary text encoded as UTF-8.

* **Review:** one of `neutral`, `negative`, `positive`, or `lost_interest`.
  This may be used as an adjective (e.g. "positive commentary").

* **Cancels List:** list of other commentary sub-objects
  with the *same parent* schedule node.

A commentary is **cancelled** if it appears in any cancels list.
The "review" of a schedule node is derived from its **non-cancelled**
commentary sub-objects:

* At least one positive and one negative: `mixed`

* Otherwise, at least one positive: `positive`

* Otherwise, at least one negative: `negative`

* Otherwise, at least one lost-interest: `lost_interest`

* Otherwise, `neutral`


## WarningToggle Sub-object State

* **WarningToggle Full ID:**
  `{parent schedule full ID}_{timestamp}`

* **Citation:** Reference to commentary to cite.
  This can be any commentary in the entire catalog.

* **Value:** either a `(warning rule name, function name)` pair identifying
  a warning to block, or, the ID of another `WarningToggle` to cancel
  (i.e. re-enable blocked warning).
  This is a tagged union: a `WarningToggle` is *exactly one* of a block or a
  cancel, never both and never neither.

The warning-is-blocked algorithm, for a given schedule node:

* Collect the set `W` of `WarningToggle` objects owned by schedule nodes
  on the path from the given schedule node to its tree root.

* Subtract from `W` the set of `WarningToggle` objects cancelled by
  any `WarningToggle` object in `W`.

* The set of `(warning rule name, function name)` pairs carried by the
  surviving `WarningToggle` objects identify the warnings blocked.

This localizes the effect of a schedule node's `WarningToggle` -- both
blocks and `WarningToggle` cancellations -- to the subtree of the
schedule node.


## Benchmark Sub-object State

**Benchmark Full ID:** `{parent schedule node full ID}_{hostname}_{timestamp}`.

JSON object with key value pairs:

* `hostname`: string, not sanitized

* `cpu_count`: number, CPU count of system used for profiling

* `timestamp`

* `parameters`: object, generator parameters used to generate the profiled Halide binary

* `parameters_index`: number, index of said parameters in the schedule node's
  `generator_parameters.json`.

* `problem`: string, full ID of problem used for runner command

* `profiler`: the profiler JSON output should be a JSON object whose "pipelines"
  value is a list of 1 object. This is that inner object.
  (There will be more than 1 if we support multiple generators; just error for != 1 for now).

* `warnings`: list of warning objects captured from profiler output,
  stored in a temporary format (`HL_PROFILER_JSON_TEMPORARY_WARNINGS` output).

* `stdout`: stdout captured from the profiled Halide binary

Note this is not the profiler you'll find documented on the internet.
The profiler was rewritten internally for this project.

FUTURE: once profile tool accepts explicit input sizes etc.
we need to embed that in here.


## Benchmark Set State

**Benchmark Set Full ID:** `{sanitized hostname}_{timestamp}`
<!-- impl -->

Rationale: timestamp alone would be reasonable for one machine
(would uniquify even on collisions due to minted timestamp behavior).
Minting will fail if we parallelize across multiple machines.
Using the computer name to unique-ify makes sense since it's unreasonable
to expect comparing profiler runs on different machines to make sense.
(I hope I don't live to eat these words -- but breaking changes are AOK for now).
<!-- end impl -->

3-level JSON object, created by `build` tool profiler feature.

This `object` is indexed as

    object[schedule full ID][generator parameters index][batch number]

The set of top-level keys gives the set of schedule nodes profiled
by the creator `build` tool usage.
The list `object[schedule full ID][generator parameters index]` is of
length batch-count.
Each element is a string: benchmark sub-object full ID.

So, the set of all IDs `object[*][*][i]` references the set of benchmarks
created on the i-th batch of the tool usage.


## Problem Object State

* **Command Line Arguments:** `argv`, with some `<...>` placeholder values.

* **Enablement State:** `enabled`, `disabled`, or `main`.

* **Short Name:** string with only alphanumeric characters and underscores.

* **Problem Object Full ID:** hash of commands only.

The "enabled problems" are those with `enabled` or `main` state.

The "main problem" is the unique problem object with `main` state;
tools that require this give an error if this is not well-defined.

See the help text for `new_problem` for more information.


## Golden Object State

* **Timestamp**

* **Golden Object Full ID:** `golden_{timestamp}`

* **Remarks:** text.

* **Schedule:** reference to schedule node, or none.

The **Golden Schedule Node** is the schedule node referenced by the most recent golden object.
There is no golden schedule node if the reference is none, or there's no golden object at all.

See the help for the `new_golden` tool for more information.


## Current Idea State

We need to keep track of which idea the schedule in the workspace should be parented to.
The "current idea state" stored in the current session is a tagged union of

* **No current idea state**: contains a timestamp;
  indicates the workspace schedule is to become a root node.

* **Some current idea state**: contains full ID of an idea node.


## Current Anchor Schedule

Each session may reference a single "anchor schedule", or none at all,
as part of its session private workspace state.
Wall time costs are based on relative comparison to the anchor schedule,
except for direct 2-way comparisons.


## Private Idea List

List of idea nodes stored in session private workspace state.
Each is associated with a string "pool tag" and a cost.
This is used to build a "frontier" of ideas for the
session's agent to explore (`list_private_ideas` tool).


## Private Benchmark Set List

List of benchmark sets stored in session private workspace state.
These are the benchmarks considered for schedule cost and comparison.
This is technically a set: duplicate benchmark sets are eliminated.


## Session Tree Concepts

The tools only construct two kinds of parent-session-to-child-session edges:

* **Sub-session Edges:** From parent to child with the child having depth one greater.
  Session nodes reachable from node N via only such edges are sub-sessions of N.

* **Successor-session Edges:** Between two top-level (depth 0) session nodes.
  Session nodes reachable from node N via only such edges are sucessors of N.

From this there's two derived states:

* Sessions are **self-closed** if they have an output schedule node or are delisted.
  A session is **closed** if it's self-closed or a sub-session of a self-closed session.
  A session is **open** otherwise.

* A session is a **terminus** if it is top-level, has no successor sessions, and is not delisted.
  Generally, there should be exactly one terminus, and futher progress should start from there.


## Terminus Schedule ("Final Result")

The catalog is a tree of schedules,
so it's not necessarily clear which one is the "final" schedule.

The convention is this: there usually should be only one terminus,
it should be closed,
and its primary output schedule is the "final result" of LLM-guided scheduling so far.

Advice: there should be only one top-level (main) agent,
working on a level 0 session.
All concurrency should be from sub-agents,
assigned sub-sessions of level 1+ depth.
This serializes creation of top-level sessions,
preventing unexpected multiple termini.


# Full and Short IDs

The IDs previously defined for idea and schedule nodes are the full IDs.
Only full IDs are stored in the catalog, because they are stable over time.
For convenience, short IDs are preferred almost everywhere else instead.

Short IDs contain at least one `.` OR contain only hex characters.
Full IDs contain no `.` and at least one `_`.

Each short ID matches some number of nodes or sub-objects.
The short ID resolves successfully iff it matches exactly 1 node or sub-object.
If there's more than 1 match, the error message lists all matching IDs from oldest-to-newest.
The timestamp of an idea is implicitly the timestamp of its parent schedule (break ties arbitrarily).

Tools output short IDs whenever possible, using at least 6 hash
characters (like git) to minimize the risk of ambiguity.
If a generated short ID would still be ambiguous, the tool falls back
to the full ID.

FUTURE: an override may be added to force a particular short-ID format.

Unless otherwise stated, any of the `{...}` components may be empty.

**Idea node short ID:**

* `{hash prefix}.{proposal name prefix}`:
  matches idea nodes whose proposal name starts with `{proposal name prefix}`
  and parent node's hash starts with `{hash prefix}`.

**Schedule node short ID:**

* `{idea node short ID}.canon`:
  find all idea nodes matching the given short ID,
  and match canonical schedules of such idea nodes.
  (This form has two `.`, one hidden in the `{idea node short ID}`).
  The tool prefers outputting this form of short ID when possible.

* `{idea node short ID}.{hash prefix}`:
  find all idea nodes matching the given short ID,
  and match any child schedule node of such idea nodes
  whose hash starts with `{hash prefix}`.
  (This form has two `.`, one hidden in the `{idea node short ID}`).

* `root.{hash prefix}`:
  matches all root node schedules whose hash starts with `{hash prefix}`,
  which cannot be empty.

* `{hash prefix}`:
  matches all schedule nodes whose hash starts with `{hash prefix}`,
  which cannot be empty.
  The tool accepts but does not generate short IDs of this form.

**Commentary short ID:**

* `{schedule ID}.{hash prefix}`:
  Find all schedule nodes matching the given ID (long or short),
  then match any commentary sub-objects of those schedule nodes
  that have a hash starting with `{hash prefix}`.

**Benchmark short ID:**

* `private.{schedule ID}.{generator parameters index}.{n}`:
  Matches the n-th benchmark created by the *current session*
  for the given (schedule, generator parameters) pair.
  The short ID mappings are stored in session private workspace state.
<!-- impl -->

  I had to change these from an older scheme due to too much agent confusion.

  Schedule ID could be long or short; first `.` always separates the `private`
  and the last two `.` always separate the generator parameters index and `n`.
<!-- end impl -->

**WarningToggle short ID:**

* `{schedule ID}.{timestamp}`
  Find all schedule nodes matching the given ID (long or short),
  then match any `WarningToggle` sub-objects of those schedule nodes
  that have a matching timestamp.

**Problem Short ID:**

* `problem.{short name}`:
  Match all *enabled* problem objects with the given short name.

* `main`:
  The main problem.
  The tool accepts but does not generate short IDs of this form.
<!-- impl -->

Should add more "ID Translation Tools" if more short IDs are defined.
<!-- end impl -->

**Warning:** short IDs may become invalid due to new ambiguities.
Use them only as convenient IDs for immediate tool use,
and not long-term identification (e.g. in commentary text).
Use the `schedule_full_id` and related tools.

Currently benchmark set and golden objects don't have short IDs.


# Session Handles

Session full IDs don't get a short ID form like schedules and ideas.
Instead, since almost every `dh_hl` tool invocation requires both the catalog directory and current session,
we provide an extremely terse "handle" syntax that stands-in for that required pair.
These handles are only meaningful *on a single machine*;
they are not part of the catalog state.

These handles are of the form `tmp.` followed by a series of hex digits.
They are lazily allocated for each unique `(catalog directory, session node full ID)` pair.

**Warning:** as before, do not use session handles for long-term identification.
The `tmp.` prefix is to emphasize how fragile these handles are.
If you need to identify a session in commentary or other text checked-in to the catalog,
use the full session ID.

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


# The Machine Directory

This directory stores the machine lock (for profiling)
and the state needed for session handle translation.

It is in `~/.cache/dendritic_hl/`,
with the `~/.cache` portion overridable with the `XDG_CACHE_HOME` environment variable.


## Locking

All tools acquire the **machine lock**, usually concurrently.
The profiling step of `build` and any `exec_exclusive` command
acquire the machine lock exclusively.

All tools that access a catalog acquire an exclusive per-catalog **catalog lock**.

All tools that require a current session (`-s`) acquire an exclusive per-session **session lock**,
except for a subset of read-only commands, marked when their syntax is introduced.
This locking *never* fails for correct usage: failure to acquire is diagnosed as an error
(two concurrent agents using the same session).

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


# Cost Comparison Methodology

For now, we will use real runtime performance as the cost model.
This exposes us to a various real-world noise.
The machine lock gives some protection, but is fairly useless
for long term drift (e.g. gradual CPU heat increase).

To combat drift, we profile short "batches" of different schedule nodes,
and only do direct comparisons between results in the same batch.
See `dh_hl build` for details on batched profiling.

For now, we will use the `wall_time_min` statistic as the raw cost;
the other profiler statistics are used only for the `dh_hl json_profiler_stats`
tool and the table created by the profiler.

We currently support multiple problems (e.g. multiple input shapes)
but don't have a plan to aggregate their results,
so all cost calculation is done for one specific given specific problem for now.

Schedule nodes may really correspond to multiple schedules due to the
generator parameters feature.
For the below methods, the **representative** is picked by selecting
the generator parameters object that led to the lowest median raw cost,
considering only the batches relevant for the method.

Ties are broken arbitrarily.


## 2-way Cost Comparison

When comparing two schedules head-to-head
(e.g. to answer "is there a performance regression?"),
the answer will be based only on batches that included the two schedules,
for one specific problem.

Select the representative for each schedule.
Then reduce each batch to a single sample:
the difference (schedule A raw cost - schedule B raw cost).
The samples are *paired* by batch, which cancels common-mode drift.
Compute the X% confidence interval (CI) of the *median* of these samples,
by percentile bootstrap (`B` resamples; configurable confidence, default 95%).

If the lower and upper bounds of the CI are both positive,
confidently conclude schedule A has higher cost.
If the lower and upper bounds of the CI are both negative,
confidently conclude schedule B has higher cost.

If neither is the case, then the comparison is inconclusive.
See impl.md "Cost Model Core" for the precise bootstrap procedure.


## Cost Ranking With Anchor Schedule

We use a different method to rank arbitrarily lists of schedules,
because it would be cost prohibitive to require profiling all of them
in a big batch.
The technique relies on a selection of a single anchor schedule.

For each target schedule (to be ranked), consider only batches that
included both the anchor node and the target schedule, for a specific problem.
Pick the representative for both schedules.
Reduce each batch to a single sample:
the target schedule's raw cost divided by the anchor schedule's raw cost.
The cost metric for the target schedule is the median of the samples.

This is imperfect, as various outside factors may penalize one
schedule more than the other (e.g. if one is memory bound, one compute
bound); however, this is better than nothing, and we don't use the
anchor schedule technique for high-stakes "is regression" 2-way comparisons.


## Cost Ranking Without Anchor Schedule

If there is no anchor schedule, the cost ranking falls back to raw time,
rather than dimensionless ratios.

For each schedule, consider batches that used the specific problem
and included profiling that schedule.
The cost is the median raw cost of the representative
(the same metric used to pick the representative in the first place).

This exposes the harness user to drift.
<!-- impl -->


## Cost Model Benchmark Search Warnings

This applies to `json_ranking_cost` and `json_compare_cost`
but not `list_private_ideas` due to excessive noise from ranking so many ideas.
Can be debugged by the harness user with `json_ranking_cost` individually.

Print a warning to `stderr` if 0 batches were found.

The warning gives a breakdown of the benchmarks found for each filter criterion:

* Number of benchmarks found filtering only by the first schedule node
  (target or LHS)

* If applicable, number of benchmarks left after filtering by the second schedule node
  (anchor or RHS)

* Then, number of benchmarks left after filtering by problem
  (this is always 0 given the warning is emitted,
  but clues-in the harness user as to another reason for lossage).

Suggest `dh_hl init_build --target ...`
and `dh_hl build --profile ... --problem ...`.

For 2-way comparisons, include `--other ...` in the `init_build` suggestion.
For ranking cost, include `--anchor ...` in the `init_build` suggestion
unless `--anchor auto` is in effect.

Replace all `...` with real arguments (except `--profile ...`).
<!-- end impl -->


<!--
  FORMAT CONTRACT for the code (main.py `_parse_idea_sections`): `dh_hl help`
  renders its docs from this "# Tools" section, so keep the shape:
  * The prose between this "# Tools" heading and the first heading below it
    (a "## ..." group heading or a "### ..." tool section) is printed verbatim
    by `dh_hl help` (no argument) as the common usage notes.
  * Tools are grouped under "## ..." group headings; the group prose before a
    group's first "### " tool section is NOT part of any tool's help.
  * Each tool is a "### <Name> Tool(s)" heading whose FIRST indented block is a
    synopsis with one "dh_hl <command> ..." line per command it documents;
    `dh_hl help <command>` locates the section by that command and prints it.
    One section may document several commands (they render together).
  * "NOTE: [link ...]" lines are stripped from the rendered help output, as are
    all HTML comments and the `impl`/`end impl` detail regions they fence
    (`prompts.render_idea_help`); `help`/`end help` regions are KEPT in the help
    output but dropped from the assembled prompt.
  * The same four fence words + no-nesting rule as prompt_common.md apply here
    (one shared engine, `prompts.render_fenced`).  `dh_hl help` is audience-
    neutral, so it keeps BOTH `main` and `sub` regions; the assembled prompt
    picks one audience.  See the FORMAT CONTRACT atop prompt_common.md.
-->
# Tools

The tools are invoked with `dh_hl {tool name} args...`.
There are two frequest arguments:

* `-C`, `--catalog`: gives the directory name for the current catalog.
  The `.dh_hl` extension is required only by `new_catalog` (the naming
  convention is enforced at creation); every other tool accepts whatever
  catalog directory it is handed.

* `-s`, `--session`: gives the session node full ID OR a session handle.
  A session handle may substitute for a mandatory `-C` argument;
  if both are given, the catalog directory must match.

Tools that *require* a current session have `-s` shown as an explicit argument,
but note `-C` is implicitly required if `-s` passed a session node full ID.
Tools that *require* only a catalog directory have `-C` shown as an explicit argument.
However, all tools accept both arguments, for simplicity.
<!-- impl -->

The human has forgotten `-C`/`-s` at least a dozen times.
Flag these mistakes when you see them
(other than commands like `dh_hl help` that truly don't need a catalog).
<!-- end impl -->

The "current session" is the session node referenced by the above 2 arguments.
The "current idea state" and "workspace C++ file" implicitly refer to the
corresponding session private workspace state.

`{...}` (curly brackets) means a mandatory argument.

`[...]` (square brackets) means an optional argument.

`-` means stdin for any input file argument.

`[schedule ID]`, if not given explicitly,
implies the unambiguous schedule node ID that would be given by `dh_hl status`,
or an error if no current session (`-s`) or no unambiguous schedule node ID would be given.
It is rarely needed to pass this.
All `schedule ID` arguments also accept the following magic values:

* `terminus`: primary output schedule of terminus (error if not well-defined)

* `session_output`: primary output schedule of the current session (`-s` required)

* `golden`: golden schedule node (error if none)

* any golden object ID: schedule node of the referenced golden (error if none)
<!-- impl -->

IMPL TASK: implement the `terminus` and `session_output` translations.
The `golden` value and golden object IDs are done (handled in
`catalog._resolve_schedule`, so `--other golden` and every `[schedule ID]`
argument pick them up).  `terminus`/`session_output` still resolve only through
their dedicated tools; wiring them into `_resolve_schedule` needs session
context and is bundled with the dedicated-tool elimination.
Can lift code from `terminus_schedule_full_id`, etc.  `seed` intentionally removed.

The golden object IDs have a form that will never collide with
schedule full IDs (can't start with `golden_`)
or short IDs (which always have a `.`).
<!-- end impl -->

The "current idea node" is nothing, if the current idea state encodes "no current idea",
and otherwise the idea node referenced by the "some current idea" state.
Commands that explicitly edit the current idea state must not error out
due to errors in the existing `current_idea_state.txt`.

Tools that print an ID or such with no other output still
include a trailing newline, as customary for Unix tools.


## Harness Usage Help Tools

### Help Tool

    dh_hl help [command]

With no `[command]`, lists all commands briefly;
with a `[command]`, gives the full help for that one.

Most complicated CLI commands are greatly sumamrized in the prompt
(progressive discovery pattern).
If you are confused by their usage or output format, DON'T GUESS.
Use the `help` tool to understand their real semantics.
<!-- impl -->


### Help Tool — Implementation Details

Both help views render from **this repo's `idea.md`** (the single source), via
`main._parse_idea_sections()`, which returns `(intro, mapping)`:

* `dh_hl help` (no arg) lists the `COMMAND_HELP` one-liners, then prints the
  `intro` — the prose between the "# Tools" heading and the first heading below
  it (the shared argument conventions).
* `dh_hl help <command>` prints `mapping[command]` — the detailed "### ... Tool"
  section, keyed by the commands in each section's leading indented `dh_hl <cmd>`
  synopsis block (not by heading name), so a multi-command section like "Copy
  Schedule, ID-of Schedule Tools" maps all its commands to the same shared text.

`_parse_idea_sections` first runs the raw idea.md through
`prompts.render_idea_help`, which drops the `<!-- impl -->`..`<!-- end impl -->`
detail regions (implementer notes) but keeps the `<!-- help -->` regions (and,
being audience-neutral, keeps both `main` and `sub` regions), and strips every
other HTML comment — the same fence engine the `prompt` tool uses
(`prompts.render_fenced`), just with `audience=None` and removing only `impl`
instead of both details.
`NOTE: [link…]` lines are then stripped too.  The format
`_parse_idea_sections` relies on is spelled out in a FORMAT CONTRACT comment just
above "# Tools" in `idea.md`.  `idea.md` lives one level above the package dir,
so a copy run detached from the repo won't find it — `help` then degrades to the
command list / one-liner (no crash).

Doc/code stay bound by a test (`tests/test_help.py`) asserting the CLI command
set equals the set of commands `_parse_idea_help()` finds — add a command
without an idea.md tool section (or vice versa) and it fails.
<!-- end impl -->


### Harness Prompt Tools

    dh_hl prompt --main
    dh_hl prompt --sub
    dh_hl detail {name}
    dh_hl examples {name}

The `prompt` tool prints the standing agent prompt,
for either the main-agent (`--main`) or sub-agent (`--sub`) audience.
<!-- help -->

Exactly one of `--main`/`--sub` is required.
The audience is deliberately **not** inferred from the current session,
so the prompt can double-check that the agent is running with the role it thinks
it is (e.g. that a spawned sub-agent wasn't handed a main-agent's session).
<!-- end help -->

The prompt mentions supplemental documents in the `detail/` or
`examples/` directory, which are part of the harness source repo.
The `detail` and `examples` tools fetch a named file from those
respective directories and prints it to `stdout`.
<!-- impl -->

The `detail` tool quietly retries with `.md` appended if the file was not found.
The `examples` tool quietly retries with `.cpp` appended if the file was not found.
Agents claim that some docs omit the extension in example commands,
and I can't find the offenders (which may not even exist)
so I just have this workaround.
Note: you cannot just blindly append the extension;
sometimes you really want a non `.md`/`.cpp` file (e.g. `.hpp`).

<!-- end impl -->
<!-- impl -->


### Harness Prompt Tools — Implementation Details

All three live in `prompts.py` (the assembly logic) with thin `cmd_*` wrappers
in `tools.py`; none needs a catalog or session (they read the harness *source*
repo, one level above the package dir, via `prompts._REPO_DIR`).

`prompt` (`prompts.load_prompt`) concatenates four processed docs, in order,
separated by a single blank line:

* `prompt_common.md`, with main/sub-agent specialization applied AND HTML
  comments removed (`parse_prompt`, below).

* `idea.md`, with main/sub-agent specialization applied, details removed, and
  HTML comments removed (the SAME `parse_prompt` engine as prompt_common.md)

* `loopdoc.md`, with HTML comments removed

* `adams_opus_scheduling_guide.md`, with HTML comments removed

The doc list is `prompts._PROMPT_DOCS`; `load_prompt(audience, path)` reads
`prompt_common.md` from `path` and the other docs from that same directory, so a
detached copy missing any of them errors cleanly (`_read_source` →
`DhHlError`).  "HTML comments removed" is `prompts.strip_html_comments`: a
non-greedy `<!--.*?-->` (DOTALL) substitution that drops inline *and*
multi-line comments, followed by `_collapse_blanks`.

`detail`/`examples` (`prompts.load_doc(kind, name)`) print a named file from the
harness source `detail/` or `examples/` directory, applying the same HTML-comment
removal but ONLY to Markdown files (`name.endswith(".md")`); other files (e.g.
example `.cpp`/`.hpp`) are emitted verbatim.

**Sanitization (implemented).** The sole filename check is
`os.path.split(name)[0] == ""`: `name` must have no directory part.  This
rejects every path separator form — a leading `/` (absolute), any embedded
`sub/x`, a `../` escape, and a trailing `foo/` — because `os.path.split` puts
all of those in the head.  A bare `.` or `..` slips through the split check
(head is `""`) but then `open()` hits the directory and raises `IsADirectoryError`,
which is caught and reported as a clean "cannot read {kind} file" `DhHlError`.
So directory traversal is impossible: reads are confined to a direct child of
the fixed `detail/`/`examples/` directory.  A missing file is likewise a clean
`DhHlError`, not a traceback.

**One fence engine for both docs (`prompts.render_fenced`).** `prompt_common.md`
and `idea.md` share ONE processor.  Content is COMMON unless wrapped in a *fence*
— an HTML comment whose only word is one of four, on two axes: audience
(`main`/`sub`) and detail (`help`/`impl`), each closed by a matching
`end <word>`.  A view is `(audience, remove_detail)`: it drops the non-matching
audience's regions (or none, when `audience=None`) and the regions whose detail
word is in `remove_detail`, plus every fence line and HTML comment, then collapses
the blank runs so the output reads cleanly.  Fences do **not** nest — at most one
is open at a time, of any word — so a maintainer note wanted inside an open region
is a plain multi-word HTML comment (stripped from every view) rather than a nested
fence.

`parse_prompt(text, audience, source)` is the prompt view: pick the audience,
remove BOTH details.  `load_prompt` calls it on prompt_common.md AND idea.md, so
idea.md's `<!-- main -->` sections are audience-specialized just like
prompt_common.md's (that is why idea.md is not merely "HTML comments removed").
`render_idea_help(text)` is the `dh_hl help` view: `audience=None` (keep both
audiences), remove only `impl` (see "Help Tool — Implementation Details").

The audience is **explicit only** — never inferred from the session — so the
prompt can double-check the agent's role (e.g. catch a sub-agent that was handed
a main session).  argparse makes `--main`/`--sub` a required mutually-exclusive
pair.

`render_fenced` is the format guard: it raises `DhHlError` on any nesting, an
unmatched/dangling fence, or a fence-shaped comment naming a word other than the
four (single-word comments are reserved for fences, so a typo fails loudly rather
than silently leaking a region).  The rules are spelled out in a FORMAT CONTRACT
comment atop `prompt_common.md` (and above "# Tools" in idea.md).  Like idea.md,
prompt_common.md sits above the package dir; if missing, `prompt` errors cleanly
(no fallback — the prompt has no default content).  Covered by
`tests/test_prompt.py`.
<!-- end impl -->


## Session Creation Tools

Each session creation tool requires (or implies) an input proposal name,
prompt file, and list of parent schedule nodes.

The tools perform the steps:

* A new session ID is allocated.

* For each parent schedule node,
  a new idea node is created from the proposal name and prompt file,
  in the same manner as
  `dh_hl new_idea {proposal name} {prompt file} {parent schedule ID}`,
  except that,
  (a) the pool tag of the new idea is `session.{proposal name}`
  (b) the proposal text has the line `Created for session: {session_id}` appended.

* Create a new session seeded with the new idea nodes created above,
  and with the prompt from the prompt file.
  The session private workspace is not initialized.
  The new session's parent session and default anchor node is defined per-tool.
  The session also snapshots the "golden schedule node on opening" and
  "enabled problems on opening" as they exist at this moment.

* For each seed idea, a new schedule node is created,
  holding a copy of the seed idea's parent's C++ and parameters files.
  This is immediately set as the canonical schedule of the new idea node.

* Allocate a session handle for the new session, and print it.

The duplicate schedule node is somewhat hacky,
but ensures that a new session can immediately assume it's given
an exclusive sub-tree to explore.
It's expected the prompt will be fairly "high level",
and not comparable to most idea nodes in complexity.
So, the agent can start generating more short-term ideas
for a parent schedule that's exclusively its own.


### New Catalog Tool

    dh_hl new_catalog -C ... {proposal name} {prompt file} {input C++ file} [input generator parameters]

Creates a new catalog directory with the bare minimum state to get started:

* Two schedule nodes, both holding a copy of the input C++ file and
  input generator parameters file.
  If the optional parameters aren't given, default to `[{}]`
  ("benchmark once with no parameters").

* One idea node connecting the two schedule nodes.

* One top-level session node (terminus) seeded with that idea node.

* A problem object with short name `default`, state `main`,
  and CLI `<RunGenMain> --benchmarks=all --estimate_all`.
  Note this problem only works for generators that include
  `set_estimate` for all input sizes.

* A golden is intentionally NOT added by default.
<!-- help -->

The new catalog directory is named by the `-C` argument.
The requirement for `-C` is *opposite* all other commands:
it is an error if the named directory *does* exist.

The behavior is as-if a single schedule node were created,
then a new session is created with that schedule as the only parent schedule.
The new session node has no parent session and has no default anchor node.
This is because the user-provided schedule may be very poor,
so it's not a reasonable default as an anchor (profiling may never terminate)
<!-- end help -->


### New Sub Session Tool

    dh_hl new_sub_session -s ... {proposal name} {prompt file} [schedule IDs...]

Create a new sub-session,
which is a child of the current session with 1 greater depth.

The `[schedule IDs...]` is a list of schedule node IDs
(each ID is a separate `argv` argument);
these are the parent schedules for new session creation.
An empty list behaves like the default `[schedule ID]` argument.

The default anchor is the current anchor of the current session
(which could be none).


### New Successor Session Tool

    dh_hl new_successor_session -s ... {proposal name} {prompt file}

The current session must be self-closed and have depth 0.

Create a new successor session (depth = 0) with

* the current session as its parent.

* the output schedules of the current session as
  the parent schedules for session creation.

* the primary output of the current session as
  its default anchor.


## Session Workspace Files Tools

### Init Workspace Tool

    dh_hl init_workspace -s ...

Initialize the session private workspace state to defaults.
Unless the optional `--force` flag is given, the tool fails if any
existing state would be overwritten.
<!-- help -->

**Defaults:**

* **Generator, Generator Parameters, Current Idea State:**
  initialized as if by `dh_hl restore_idea` done on the 0th seed idea.

* **Current Anchor Schedule:**
  initialized from the current session's default anchor schedule;
  no current anchor if no default anchor schedule.

* **Private Idea List:** initialized from the current session's seed ideas,
  each with pool tag `default`.
  This is intentionally different from the parent session's pool tags;
  the new agent can make their own decisions what to prioritize.

* **Private Benchmark Set List:** empty.

The session lock is low-level state that is not exclusive to this tool;
it is implicitly created without any user action.
<!-- end help -->
<!-- impl -->


### Init Workspace Tool -- Implementation Details

(Implemented by writing each file via
`safety.write_allowed(..., allow=<--force>)`: with `--force` off, an existing
target hits `new_file`'s `O_EXCL` create and raises, which the tool catches to
print the AGENTS warning below.)

Direct reads of the workspace files (`generator.cpp`, `generator_parameters.json`)
are funneled through a helper that, on a missing file, gives a friendly
"run `init_workspace`" notice naming the missing path rather than a raw
Python traceback.

If the tool fails because some workspace state already exists (without
`--force`), it prints one of the following AGENTS warnings:

    # Session depth == 0
    AGENTS: the session seems to already be initialized,
    as if in use by (or previously used by) another agent.
    Things will fail badly if this session is used concurrently.
    If you can speak with the user interactively, ask for a decision:

    1. the user finds the conversation that was for this session
    and asks that agent to close the session (preferred)

    2. inspect the current session workspace and try to pick up
    where the previous agent left off.

    3. restart the session from scratch (re-run this tool with --force)

    If you can't ask (e.g. automated workflow),
    don't continue, unless other prompting provides an expected fix.

    # Session depth != 0
    AGENTS: the session seems to already be initialized,
    as if it's in use by (or previously used by) another agent.
    STOP IMMEDIATELY and report to the main agent or user what happened.
    You can do so normally, not via `dh_hl close_session`.
<!-- end impl -->


### Workspace Location Tools

    dh_hl workspace_schedule -s ...
    dh_hl workspace_parameters -s ...
    dh_hl workspace_bin -s ...

Respectively, get the filename of the

* workspace C++ file

* workspace generator parameters JSON file

* bin directory

AGENTS: use `init_workspace` and not these tools to create the
new generator and parameters files.
Nevertheless, these tools do not enforce this in case extenuating
circumstances require a deviation.


### Status Tool

    # Does not acquire session lock
    dh_hl status -s ...

This is a purely read-only command.
<!-- Agents used to have to run this on startup. -->
<!-- The safety this provided got replaced with init_workspace. -->

If there was no current session given, the tool errors.
Otherwise, the tool tries to find a schedule node that already holds
a copy of the workspace files,
and give basic information on the current catalog state.

<!-- help -->
**Outputs:**

* The full IDs of the current session and its parent session (if any)

* The is-delisted flag of the current session

* Whether the current session is `open` or `closed`.

* The current idea state,
  whether the current idea node exists,
  and the canonical schedule for it, if any.

* The ID of the **unambiguous schedule node**, if it exists.
  This is the schedule node that holds a copy of the
  workspace files (matched by hash)
  and has a parenting status matching the current idea state:
    - **no current idea:**
      is a root node whose timestamp matches the current idea state
    - **some current idea:**
      its parent is the current idea node.

* The status as one of
    - `missing workspace generator.cpp`
    - `missing workspace generator_parameters.json`
    - `missing workspace generator.cpp and generator_parameters.json`
    - `workspace inconsistent, unknown schedule`
      (could not find any stored schedule matching the current workspace files)
    - `workspace inconsistent, unexpected current idea state`
      (found stored schedule in catalog, but none were unambiguous)
    - `workspace consistent`
      (unambiguous schedule node found)

**Rationale:**

A workspace is in "consistent state"
when it unambiguously corresponds to a schedule node whose
parent idea is what we expected.
Essentially, this was "where we left off" when we last stopped searching.
As soon as we start editing the file, it'll be in inconsistent state.

We need the current idea state to remember what idea we were working on,
since we have no idea otherwise as soon as the schedule hash changes.

*Merge risk:* since the private current idea state is not git tracked,
it's possible heavy-handed git actions could cause the current idea
state to desync from the real catalog.
This is why the command is robust to nonexistent current idea node IDs.
<!-- end help -->
<!-- impl -->
**Search Implementation Details:**

Hash the workspace files and look for schedule nodes with matching hashes.

If either workspace file is missing, the status is the matching
`missing workspace ...` value, naming whichever of `generator.cpp` /
`generator_parameters.json` is absent (both, if both).

Otherwise, if no hash matches exist,
the status is "workspace inconsistent, unknown schedule".

Otherwise, if the current idea state is parsable and holds the "no current idea" state,
and there exists a schedule node that
(a) has a matching hash,
(b) is a root node,
(c) has a timestamp matching the timestamp embedded in the current idea state,
*then* that schedule node is the **unambiguous schedule node**
and the status is "workspace consistent".

Otherwise, if the current idea state is parsable and holds the "some current idea" state,
and there exists a schedule node that
(a) has a matching hash,
(b) has its parent idea node matching the one embedded in the current idea state,
*then* that schedule node is the **unambiguous schedule node**
and the status is "workspace consistent".

Otherwise, the status is "workspace inconsistent, unexpected current idea state".

**Output Implementation Details:**

* The full IDs of the current session and its parent session (if any)

* The is-delisted flag of the current session

* Whether the current session is `open` or `closed`

* Give the current idea state
  (no current idea/some current idea/parse error/missing/etc.).
  Try to print errors cleanly if something is wrong with the state on disk.
  If the current idea node exists, print the status of its canonical schedule (none, or ID of it).
  If the current idea state is syntactically correct but references a nonexistent idea node,
  advise of that too (could happen due to a git checkout).

* Gives the status as documented previously

* If some workspace files are not found, print

        AGENTS: run `dh_hl init_workspace` to get files to edit

* If the workspace is consistent, print the ID of the unambiguous schedule node.
<!-- end impl -->


### Restore Schedule Tool

    dh_hl restore_schedule -s ... {schedule ID}

Copies the schedule node's C++ schedule and generator parameters to
the workspace, and resets the current idea state.
<!-- help -->

The current idea state depends on the schedule node's parents:

* **No parents:** set to "no current idea" state, embedding the timestamp of the schedule node.

* **One parent:** set to "some current idea" state, embedding the ID of the parent idea node.
<!-- end help -->


### Restore Idea Tool

    dh_hl restore_idea -s ... {idea ID}

Restores the private workspace to a state where you are ready
to begin *implementing* the idea.
<!-- help -->

Copies the idea's parent schedule's C++ code and
generator parameters to the workspace,
and updates the current idea state to "some current idea" state,
embedding the idea referenced in the command.

Note; the workspace will probably be inconsistent according to
`dh_hl status` after this command. This is normal.

This tool gives a warning if the referenced idea already has a canonical schedule.
The warning includes

* The ID of the canonical schedule
* A suggestion to use `restore_schedule` instead.
<!-- end help -->


## Build, Benchmark, and Warning Tools

### Init-Build Tool

    dh_hl init_build -s ...

Prepare for an up-to-3-way comparison between Halide schedules,
including possibly a new schedule node made from current workspace files.

This is the main mechanism by which new schedules enter the catalog.
The harness (by design) can only build or profile schedules in the catalog.
So this is the first step to building or profiling a new schedule.

A `build` fails if the session's most recent `init_build` failed: a failed
`init_build` clears any selection an earlier success left behind (even failures
too early to reach the tool body, caught by a pre-argparse guard).
Caveat: an `init_build` that fails before its `-s` is resolved (a missing or
malformed `-s`) can't invalidate — but a `build` with that same `-s` fails
likewise, so nothing stale is built.

Default 3 nodes: the "target node" is a copy of the current workspace files,
the "other node" is the target node's parent (if it exists),
and the anchor node is the session's current anchor schedule (if it exists).
This may be overriden with optional arguments.
<!-- help -->

* `--target {schedule ID}` or `{schedule ID}` alone
  selects the target schedule node,
  the magic `workspace` value is the default.
  <!-- lenient no --target case added due to past agent mistakes -->
  <!-- Probably they overgeneralize from other commands, which is understandable. -->

* `--other {schedule ID}` selects the other schedule node.
  The special value `none` disables the other schedule node,
  and the special value `parent` (default) selects the parent
  schedule of the target's parent idea node, if it exists,
  otherwise the other schedule node is disabled.

* `--anchor {schedule ID}` selects the anchor schedule node.
  The special value `none` disables the anchor schedule node.
  The special value `auto` selects the current session's
  current anchor if it exists,
  otherwise the anchor schedule node is disabled.
  The special value `always` is like `auto` except it's
  an error if the current session has no current anchor.

`--target workspace` behavior:

* If `dh_hl status` would give an unambiguous schedule node,
  the target schedule node is the one this tool returns.

* Otherwise, if there is no current idea node for the session,
  give an error, and suggest the `set_idea` and `new_root` tools.

* Otherwise, if the current idea node has a canonical schedule,
  the tool fails and suggests `new_idea` and `set_idea`
  (the same advice as `canon`; you branch a new idea off the canonical
  and explore there rather than piling more children onto a decided idea).

* Otherwise, add a new child schedule node to the current idea node
  holding a copy of the workspace files. This is the target node.
<!-- end help -->
<!-- impl -->

Delete any existing `init_build.json` first to ensure future
`build` commands won't run in case of failure.
This fixes a foot-gun where a failed `init_build` could go
un-noticed and cause `build` to perform stale actions.
Note private session workspace files were exempted from
the `safety` module tool safety requirements.

The "canonical schedule" refusal shares its advice text with `canon` via
`catalog.canonical_block_advice`.  It does not make the `canon`
"already has a canonical schedule" case unreachable: a non-canonical child of a
canonicalized idea can still exist (e.g. two children built under one idea, then
one made canonical), so editing the workspace to match that sibling still trips
`canon`'s check (exercised by `tests/test_advice.py`).
<!-- end impl -->


### Build Tool

    dh_hl build -s ...

Builds the schedule nodes selected by the latest `dh_hl init_build`
done with the current session (state stored in the session private workspace)
Optionally profiles them in batches (`--profile [N]`),
generating new benchmark or benchmark set objects.
Generated benchmark sets are added to the private benchmark set list.

By default, if a schedule has `N`-many generator parameters objects,
then `N` shared library and `RunGenMain` binaries are built,
one for each parameters object.

It's an error if any schedule node being built has 0 parameters objects.
<!-- help -->

The tool

1. Compiles the schedules selected by `init_build` into Halide header + binaries
   in the session private workspace `bin` directory,
   along with .`.stmt` and `.conceptual.stmt` files.
   Compiler and generator outputs get piped to harness `stdout`/`stderr`.

2. (`--profile` only) runs all generated binaries with Andrew Adams's profiler,
   with new benchmark objects added to the profiled code's source schedule node.
   The new objects' IDs are printed.
   The `stdout` of the profiling runs are redirected to the benchmark sub-object.

3. Updates the result state of each built/profiled schedule node,
   monotonically (worse to better only).
   A `--only [int]` build caps the achievable result at `halide error`,
   since not all possible generator parameters are verified.
  <!-- Note: see separate pseudocode (stripped from dh_hl help) -->
  <!-- Note: --only 0 when only 1 parameters object exists breaks the -->
  <!-- above rationale, but we don't bother with this special case. -->

Flags:

* `--problem {problem ID}`
  adds the named problem to the set of selected problems to test with.
  If no `--problem` arguments exist,
  the testing is done for all enabled problems.
  <!-- CAUTION: use "selected problem" consistently in this section,
  as "enabled problem" means something different. -->

* `--profile [N]` (`N = 0` default, must be a non-negative integer).
  This enables `N` batches of profiler runs per problem.
  Each batch runs all generated binaries in a random order ("interleaved").

* `--only [N|target|all]`.
  Limits the generators and Halide binaries built.
  `all` (default) selects the target, anchor, and other schedules
  (if they exist).
  `target` selects only the target.
  `N` (integer) selects only the target, and further generates
  only one binary, for the `N`-th generator parameters object;
  in this case `halide error` is the best result possible.

This tool exits successfully iff no harness errors occurred
and all subprocesses succeeded.

If at least 1 profiling batch occured,
and `--only all` or `--only target` are in effect,
then for each selected problem where all benchmark runs ran successfully,
a new benchmark set is generated containing all benchmark sub-objects for that problem.
<!-- end help -->

Important lines emitted by the harness itself are prefixed with
`dh_hl: ` for grep-ability.
<!-- impl -->


### Build Tool Implementation Details

It's crucial that the catalog lock is not acquired during the
compilation phase. This prevents locking out other agents
needlessly (despite they will be locked out soon by profiling).

The `... with Benchmark ID:` line leads with `...` to tie it to the preceding
`Profiled ...` line, so agents don't misread the ID as belonging to the
profiler's own printf output printed nearby.

IMPL TASK: print benchmark short IDs of the `private.{schedule}.{parameters
index}.{n}` session-local form (idea.md "Benchmark short ID").  `build` currently
prints the general `{schedule}.{host}_{ts}` short ID instead; the `private.` form
depends on the not-yet-implemented `benchmark_short_id/` session state
(impl.md "Session Private Workspace").

Pseudocode:

    # 1a. C++ compilation: relies on state from init_build
    acquire_concurrent(machine_lock)
    acquire_exclusive(session_lock)
    nodes = {target}
    if --only all and other node exists:
        nodes.add(other)
    if --only all and anchor node exists:
        nodes.add(anchor)
    for node in nodes:
        print "dh_hl: begin C++ compile: {node.short_id}"
        # ... Compile/Link C++
        # NB the generator's -f basename is baked into C identifiers in the
        # emitted registration.cpp, so any full_id used there is first sanitized
        # (non-alphanumerics -> '_', prefixed with a letter) to a valid, still-
        # unique C identifier; plain bin/ file names can keep the raw full_id.
        # Ninja file in session private workspace: bin/{node.full_id}.ninja
        # Generator in session private workspace: bin/{node.full_id}_generator
        # Use similar ID-based naming for intermediate .o files / registration etc.
        # Full ID keying prevents redundant rebuilds
        print "dh_hl: end C++ compile (success|fail)"

    # 1b. Halide generators
    for node in nodes:
        for i, params in enumerate(node.generator_parameters):
            if (--only i or --only all) and C++ compilation of node succeeded:
                print "dh_hl: begin Halide generator {i}: {node.short_id}"
                print "dh_hl: params={params}"
                # ... Run Halide generator with given params
                # Build outputs placed into: bin/{node.full_id}_{i}/...
                print "dh_hl: end Halide generator {i} (success|fail)"

    # 2. Profiling
    if --profile N with N == 0 or any build/generate failed:
        acquire_exclusive(catalog_lock)
    else:
        acquire_exclusive(machine_lock)  # Upgrade from concurrent
        acquire_exclusive(catalog_lock)
        binaries = []
        for node in nodes:
            for params in node.generator_parameters:
                if Halide generator succeeded:
                    binaries.append(...)
        for problem in selected problems:
            for batch in range(N):
                shuffle(binaries)  # Shuffled each time
                for bin in binaries:
                    profile(bin)  # By executing the problem command line.
                    node, params_index = source_of(binary)
                    print "dh_hl: Profiled {node.short_id}, binary {params_index}, problem {problem.short_id} (success|fail)"
                    if success:
                        if profiler output not found:
                            # some message
                        else:
                            Add benchmark sub-object to binary's source schedule node
                            Timestamp could be taken before or after profiling, unimportant
                            print "dh_hl: ... with Benchmark ID: {benchmark short id}"

    # 3. Save results
    for node in nodes:
        if C++ build of node failed:
            result = "c++ error"
        elif any generator failed or --only [int] passed:
            result = "halide error"
        else:
            result = "success"
        node.result = best_of(node.result, result)

    # Also save per-problem benchmark set object and add to session, if criteria passed.

See the [Reference Build Commands](reference_build_commands.md) file for the
tested build/link recipe.  That file teaches the **Halide toolchain** (which
compiler/generator/link commands to run, and their gotchas) using its own
example file names.  It is deliberately NOT the source of truth for the
catalog-specific `bin/` file names — those are named as in the pseudocode above
(keyed by schedule full ID + parameters index), and `build.py` owns them.
Don't try to keep the two in sync.

A runner that exits 0 but emits no (or a corrupt) profiler JSON is a "catalogued
bad outcome", not a tool failure: the profile loop skips that benchmark and keeps
going (no state rollback), the build exits nonzero, and the node still reaches
`success` (the generators built).  Tested at both tiers --
`test_build_fake.py::test_broken_runner_no_json_is_bad_outcome_not_crash` and the
real-CLI `test_shared_lib_halide.py::test_broken_runner_no_json_is_catalogued_bad_outcome`
(a broken `<Lib>` runner driven through `dh_hl build --profile`).

FUTURE: (not a task for current turn)
check the profiler output provenance is correct,
as suggested by `reference_build_commands.md`,
before trusting and ingesting its results.
This counts as a "catalogued bad outcome", not "tool failure"
(i.e. no state rollback; profile loop continues).
Write a real CLI test in the style of `test_build_cli_halide.py` for this;
maybe do something crooked to copy one schedule node's binary on top of another's.
The real reason for the check is to catch accidental shared library SNAFUs,
but I'm not sure this is easy to reproduce in a controlled test environment.

**As implemented** (`build.py`): `init_build` (`cmd_init_build`) resolves
target/other/anchor (`_resolve_target`/`_resolve_other`/`_resolve_anchor`, the
target possibly a freshly created child schedule) under the session + catalog
locks, then writes `init_build.json` (catalog-relative paths) to the private
workspace.  `build` (`cmd_build`) reads that file lock-free, then
`_compile_phase` runs phase 1a (per-node `_write_ninja` → generator exe + shared
`RunGenMain.o`) and phase 1b, per (node, params index): `_emit` the `no_runtime`
pipeline object (into `bin/{full_id}_{i}/`, `-f dh_hl_pipeline`), then `_link`
the RunGenMain `.rungen` (object + the once-emitted shared `halide_runtime.o`
from `_ensure_runtime`) and `_link_shared` the `no_runtime`
`dh_hl_pipeline.{so,dylib}`.  `stmt`/`conceptual_stmt` are emitted for every
built pipeline and fetched on demand by `copy_build_output` (no eager
`bin/{i}.stmt` copy).  Profiling is all-or-nothing on a clean build
(`do_profile = --profile > 0 and no build failure`); only then does it
`locks.upgrade_machine_exclusive()` **before** acquiring the catalog lock.
`_profile_phase` then loops the selected problems (`--problem`, default all
enabled), running the shuffled batches per problem via the problem's resolved
argv (`_resolve_run`: `<RunGenMain>`→the `.rungen`, `<Lib>`→the `.so` +
`DENDRITIC_HL_OUTPUT_LIB`), attaching a benchmark sub-object (tagged with problem
+ parameters index) to each binary's source node.  One benchmark set is minted
per selected problem whose runs all succeeded (single-problem sets, added to the
private list).  `_compute_result` derives each node's monotone result state
(`success` == all generators emitted).  A `c++ error` / `halide error` outcome
still persists the node (the result update is monotone, never a rollback); the
generator-count harness error skips the node's compile without updating its
result.
<!-- end impl -->


### Copy Build Output Tool

    dh_hl copy_build_output -s ... {output file} {what} [schedule ID]
<!-- help -->

Copy a certain build output for the given schedule node from the session private workspace.

`what` can be

* `generator`: Halide generator binary

* `algorithm_hlpipe`

* `stmt`

* `conceptual_stmt`

* `header` (declares generated pipeline as the function `dh_hl_pipeline`)

* `RunGenMain`

* `shared_library`

If there's more than 1 generator parameters object for the schedule node
and `what` is not `generator`, then `--parameters {object index}`
must be given.
<!-- end help -->


### Add Warning Toggle Tool

    dh_hl add_warning_toggle -C ... {schedule ID} {commentary ID}

Add a new `WarningToggle` sub object to the referenced schedule,
which cites the referenced commentary.

Takes exactly one of:

* `--block {rule} {func}` makes the new `WarningToggle` block
  warnings with the given rule name and function name.

* `--cancel {WarningToggle ID}` makes the `WarningToggle`
  cancel the effects of the given other object (i.e. un-block).

The schedule given by `dh_hl session_root_of` is a reasonable
default for the `{schedule ID}` argument.
<!-- help -->
This scopes the "lesson" that the warning is to be ignored mostly to
schedules worked on in this session, but not to those from other
sessions, which may be working on schedules completely different from
this one.
<!-- end help -->
<!-- impl -->

FUTURE: warning for unknown warning rule name or func name.

FUTURE: automate schedule ID, but the defaults for `[schedule ID]`
are probably not appropriate for this command.
<!-- end impl -->


### Debug Warning Toggle Tool

    dh_hl debug_warning_toggle -C ... [schedule ID]

Investigate `WarningToggle` sub-objects of the schedule and its indirect parents.
<!-- help -->
By default, the tool collects all `WarningToggle` objects using the
schedule-node-to-root algorithm specified in the `WarningToggle` state
documentation, then prints for each object:

* `id: {short ID}`

* `citation: {commentary short ID}`

* First up to 72 characters of the first line of the cited commentary.

* `rule/func: {rule name} {func name}`, only printed for objects that block a warning

* `cancels: {WarningToggle short ID}`,
  only printed for objects that cancel another `WarningToggle` object

* `cancelled: {true|false}`

There are dividers between printed objects.

This command takes further arguments:

* `--block {rule} {func}` filters the list only to objects that
  block warnings with the given (rule name, function name) pair,
  including ones where `cancelled` is true.

* `--cancel {WarningToggle ID}` filters the list only to objects
  that cancel the given `WarningToggle` object.
  It is not an error if the named object does not exist.
<!-- end help -->


### View Benchmark Warnings Tool

    dh_hl view_benchmark_warnings -C ... {benchmark ID}

Pretty-print the warnings embedded in the referenced benchmark sub-object.

<!-- help -->
Takes an optional `--always-show-message` flag.

For each warning, the tool prints:

* `rule/func: {rule name} {func name}`

* `message: {message text}`, printed only if the warning is not blocked
  for this schedule node, or if `--always-show-message` was passed.

If the warning is blocked for this schedule node (see `WarningToggle`),
that warning has additional lines:

* `blocked by: {WarningToggle ID}` (picks arbitrarily if blocked by multiple).

* `citation: {Commentary ID}`, unpacked from the `WarningToggle` referenced above.

* First up to 72 characters of the first line of the cited commentary.

There are dividers between printed warnings.

FUTURE: fix `max_warnings` limit in Halide profiler that silently drops warnings.


<!-- end help -->
### View Benchmark Stdout Tool

    dh_hl view_benchmark_stdout -C ... {benchmark ID}

Print the `stdout` captured for the named benchmark.


### JSON Benchmark Info Tool

    dh_hl json_benchmark_info -C ... {benchmark ID}

Prints the identified benchmark in benchmark sub-object JSON format.


### JSON Benchmark Set Info Tool

    dh_hl json_benchmark_set_info -C ... {benchmark set ID}

Prints the state of the referenced benchmark set as a JSON object.


## Cost Model and Private Idea List Tools

### List Session Private Ideas Tool

    dh_hl list_private_ideas -s ...

Gives a cost ranked "frontier" of session private ideas,
grouped by pool tag (as mapped by the session private workspace state).
The cost is calculated from the session's private benchmark set list,
using only benchmarks for a specific problem (the main problem, by default).
Performs automated detection of ideas "obsoleted by" lower cost child ideas.

**Optional Command Line Arguments:**

* `--anchor ...`, same behavior as in `json_ranking_cost`.

* `--confidence {ci}`, set confidence threshold for 2-way "obsoleted by"
  comparisons. `0 < ci < 1`.

* `--max {n}`, list up to `n` ideas per pool tag. Default `n = 6`.

* `--problem {problem ID}`, select the specific problem for the cost model.

* `--pool {name}`, enable including idea nodes with the given pool tag.
  Unions with other `--pool` arguments.

* `--pools {regex}`, enable including idea nodes with regex-matched pool tags.
  Unions with other `--pool`, `--pools` arguments;
  if no such arguments, all pool tags without a leading `.` are enabled
  (see `hide_private_idea`).

* `--done`, include only idea nodes with canonical schedules.

* `--todo`, include only idea nodes without canonical schedules.

<!-- help -->
**Outputs:**

For each enabled pool tag in sorted order,
the tool prints the banner `=== {pool tag} ===`.

The private ideas with that pool tag are sorted by cost.
This is the cost of the idea's canonical schedule, if it exists,
otherwise the cost of the idea's parent schedule,
calculated as in `json_ranking_cost`.
A `null` cost is sorted as if it were `0` cost
(they bubble to the top and prompt the agent to run benchmarks).

After the `--done`/`--todo` filter, the sorted idea list is truncated
as appropriate for the `--max` argument, and each is printed as if by
`list_child_ideas`, with additional information:

* `batch_count: ...`, number of batches used to compute cost for ranking.

* `cost: ...`, computed cost (`null` if 0 batches).

* one `obsoleted by: {idea ID}` line
  for each positive "obsoleted by" check (below).
  Note, the batch count for this check is not shown.

**Obsoleted By:**

This check applies only to idea nodes `P` with canonical schedules.

Check each child idea `C` of `P`'s canonical schedule.
The check is positive if all of:

* `C` has a canonical schedule.

* The `json_compare_cost` tool would conclude `C` is an improvement over `P`.

Since the `build --profile` tool by default compares against the parent idea's
parent schedule, info for this will usually be available.
<!-- end help -->
<!-- impl -->

**Implementation Notes:**

If no anchor schedule was used for cost ranking, give the warning

    Warning: ranking is drift-exposed until you set an anchor.

If an anchor schedule was used and any cost was less than `0.5`,
give the warning

    Warning: some ranked schedules were much faster than the anchor.
    This amplifies the effects of system noise; consider a new anchor.
<!-- end impl -->


### Session Current Anchor Schedule Tools

    dh_hl get_current_anchor -s ...
    dh_hl set_current_anchor -s ... [schedule ID]

Get or set the ID of the current session's current anchor schedule node.
The special value `none` is used for "no anchor node"
(both for get and set commands).


### Session Idea Node Pool Tag Tools

    dh_hl get_pool_tag -s ... {idea ID}
    dh_hl set_pool_tag -s ... {idea ID} {pool tag}
    dh_hl hide_private_idea -s ... {idea ID}

Gets or sets the pool tag assigned for the given idea node,
as stored in the private idea list.
`set_pool_tag` implicitly adds to the list if necessary.
`hide_private_idea` simply prepends a `.` to the idea node's pool tag.
<!-- help -->

`get_pool_tag` and `hide_private_idea` error out if the idea node
is not in the private idea list.

The purpose of the pool tag is to allow the agent to enforce some
diversity in the frontier of "best ideas" shown in `list_private_ideas`.
Ideas are ranked only within their pools.
<!-- end help -->


### Rename Pool Tag Tool

    dh_hl rename_pool_tag -s ... {pool tag before} {pool tag after}
<!-- help -->

Iterates over all entries in the current session's private idea list.
Each idea that has `{pool tag before}` as its pool tag
gets its pool tag updated to `{pool tag after}`.

Prints `{count} idea nodes updated`.
<!-- end help -->


### Add/Remove Private Benchmark Set Tools

    dh_hl add_private_benchmark_set -s ...
    dh_hl remove_private_benchmark_set -s ...

This is currently not so useful, as benchmark sets are not really discoverable.
<!-- help -->

Add or remove benchmark sets from the current session's private
benchmark set list.
They are passed as a list of benchmark set IDs (`...`),
which could be an empty list.
<!-- end help -->


### List Private Benchmark Sets Tool

    dh_hl list_private_benchmark_sets -s ...
<!-- help -->

Print the full IDs of all benchmark sets in the current session's
private benchmark set list.
IDs are sorted lexicographically, one per line, with no other `stdout` output.
<!-- end help -->


### JSON Ranking Cost Query Tool

    dh_hl json_ranking_cost -s ... [schedule ID]

Report the cost for the given schedule, based on "Cost Ranking" methodology.
This relies only on batches reachable from
the current session's private benchmark set list,
filtered as specified for the methodology.
<!-- help -->

The output is a JSON object with key/value pairs on separate lines:

* `batch_count`: number of batches found by cost ranking algorithm

* `cost`: number or null (null if no batches found)

* `anchor`: string or null (schedule node full ID)

* `representative`: number,
  index of generator parameters object used by the representative.

* `parameters_raw_cost`: list, each entry is a number (or null if 0 batches),
  n-th entry gives the raw cost of the program generated with the
  n-th generator parameters object, as used for representative picking.

Takes an optional `--problem {problem ID}` argument
to select the specific problem for the cost model (default: main problem).

Uses either the "Cost Ranking With Anchor Schedule" methodology
or "Cost Ranking Without Anchor Schedule" methodology,
depending on the optional `--anchor {schedule ID}` argument:

* `--anchor none`: without anchor schedule.

* `--anchor {schedule ID}`: using the given anchor schedule.

* `--anchor always`: using the session's current anchor schedule
  (error if no current anchor schedule).

* `--anchor auto`: (default behavior)
  using the session's current anchor schedule if it exists,
  otherwise without anchor schedule.
<!-- end help -->


### JSON Compare Cost Tool

    dh_hl json_compare_cost -s ... [LHS schedule ID] [RHS schedule ID]

Do a head-to-head cost comparison between the LHS and RHS schedules,
using the "2-way Cost Comparison" methodology,
done once for each enabled problem (by default),
and try to conclude if LHS is an "improvement" or a "regression" over the RHS.
This relies only on batches reachable from
the current session's private benchmark set list,
filtered as specified for the methodology.

The LHS schedule, if not explicitly given, has default `[schedule ID]` behavior.

The RHS schedule, if not explicitly given,
is the parent of the LHS's parent idea.
<!-- help -->
(Error if the parent idea doesn't exist).
Note, if you override this, you will in most cases have to run `dh_hl build`
with an explicit `--other`, as the default parent won't suffice.

The optional `--confidence {ci}` argument overrides the default
confidence for the confidence interval; must have `0 < ci < 1`.

The optional `--bootstrap {B}` argument overrides the number of bootstrap
resamples used for the confidence interval; must be at least `2`.

The optional `--problem {problem ID}` argument adds a problem to the set
of problems to run the 2-way cost comparison with.
If no `--problem` arguments are given, all enabled problems are used.

The optional `--boolean` argument converts the output to boolean form.

The default output is a list of JSON objects with key/value pairs on separate lines:

* `problem`: full ID of the problem used for the comparison

* `problem_short_id`: short ID of the problem used for the comparison

* `batch_count`: number of batches found

<!-- This is not in the default prompt due to the help/end help fence. -->
* `result`: string, "regression" if the LHS is confidently worse (higher cost)
  than the RHS, "improvement" if the LHS is confidently better (lower cost),
  "unknown" if inconclusive.
  (The RHS is the baseline: for the default RHS the LHS is the newer schedule,
  and "improvement" means it beat its parent.  This is the direction the
  `list_private_ideas` "obsoleted by" check relies on.)

* `lhs_raw_cost`: number, median raw cost of LHS representative

* `lhs_representative`: number,
  index of generator parameters object used by the LHS representative.

* `rhs_raw_cost`: number, median raw cost of RHS representative

* `rhs_representative`: number,
  index of generator parameters object used by the RHS representative.

The boolean format is a single object of the form

    {"any_improvement": bool, "any_regression": bool, "any_unknown": bool}

giving whether any of the selected per-problem cost comparisons
gave an `improvement`, `regression`, or `unknown` result, respectively.
<!-- end help -->


### JSON Profiler Statistics Tool

    dh_hl json_profiler_stats -s ... [schedule ID]

Aggregate profiler statistics for the referenced schedule,
considering only benchmarks reachable from the private benchmark set list
that profiled using the main problem (by default).
The list of stats to include is given by command line arguments.

* `-f {name}`, include a per-function statistic (e.g. `-f recompute_ratio`)

* `-p {name}`, include a pipeline-global statistic (e.g. `wall_time_mean`);
  note some names are valid for both `-f` and `-p` (e.g. `memory_peak`).

* `--parameters {n}`, consider only statistics for pipelines
  built from the n-th generator parameters object of the schedule node.
  Mandatory if there's more than one generator parameters objects included
  in the schedule node.

* `--hottest {n}`, optional, `n >= 1`.
  Output only the `n` hottest functions (defined below)

* `--problem {problem ID}`, consider only statistics from
  benchmarks run with this problem (default: `main`).
<!-- help -->

With `obj` being a benchmark sub-object, each `-p` pipeline-global
statistic name is the key name of a number value of `obj["profiler"]`,
or one of the special values:

* `active_threads`: `active_threads_numerator/active_threads_denominator`

* `allocs_per_run`: `num_allocs/runs`

Each `-f` per-function statistic name is the key name of a number value
of the objects in the `obj["profiler"]["funcs"]` list,
or one of the special values:

* `active_threads`: `active_threads_numerator/active_threads_denominator`

* `allocs_per_run`: `num_allocs/runs`

* `parallel_loops_per_run`: `parallel_loops/runs`

* `parallel_tasks_per_run`: `parallel_tasks/runs`

* `time_ratio`: `function.time_ns/pipeline.time_ns`.
  This is always included (i.e. `-f time_ratio` is implied).

FUTURE: foot-gun-y how the default stats are not divided by `runs`
and there's special `*_per_run` stats that are actually meaningful.

The output JSON object has key/value pairs

* one pair for each unique pipeline-global statistic included

* `funcs`: list of objects

The `funcs` list is sorted by highest-to-lowest median `time_ratio`,
with all profiled functions included, unless `--hottest {n}` was given,
in which case only the first `n`-many sorted functions are included.

The `func` list objects contain key/value pairs:

* `name`: string

* `parent`: number, `canonical_id` of parent func in this list (-1 if no parent)

* `canonical_id`: number for unique identification within pipeline

* one pair for each unique per-function statistic included
<!-- end help -->

Each numerical statistic is reported by aggregating all relevant
benchmarks' samples (bucketed by function, for `-f`) into a 3-list:

    [25th percentile, median, 75th percentile]

`wall_time_smallest` and any other non-number statistics are not supported.
<!-- impl -->

**Implementation Notes:**
Don't try to embed the list of allowed `-f`/`-p` values,
other than the special values described above,
so we don't have to keep updating this for profiler changes.
Just let them fail naturally when looking up JSON values,
with the error being somewhat nicer than a raw Python exception
(wrong name / non-number type).

Assert that all benchmarks found have the same number of "funcs"
and the same names for each func. You don't have to test this.

Make it so that `name`, `parent`, and `canonical_id` are printed
first in the `func` object.
<!-- end impl -->


## Commentary Tools

### Comment Tool

    dh_hl comment -C ... {commentary file} [schedule ID]

Adds a new commentary sub-object to the referenced schedule node,
with the commentary text copied from the passed `commentary file`.
Prints the ID of the new commentary sub-object.

Use the optional `--review [review]` arguments to override
the review from the default `neutral` value.
This must be a valid review type other than `mixed`.

Use the optional `--cancels [commentary ID]` arguments to add to
the cancels list of the new commentary.
Use multiple `--cancels` to create a longer list.
It's an error if any commentary object that isn't parented
to the referenced schedule node is passed.


### View Commentary Tool

    dh_hl view_commentary -C ... {commentary ID}
    dh_hl view_all_commentary -C ... [schedule ID]

Prints either the referenced commentary sub-object (`view_commentary`),
or all commentary sub-objects of the schedule (`view_all_commentary`),
with each commentary separated by a divider.

Takes an optional `--brief` argument.

Each commentary is printed as

* `timestamp: [timestamp]`

* `review: [review]`

* `cancelled: [true|false]`

* One `cancels: [commentary ID]` for each entry is the cancels list

* full text, unless `--brief` is passed, in which case only the first up to
  72 characters of the first line is printed.


### View Session Commentary Tool

    # Does not acquire session lock
    dh_hl view_session_commentary -s ...

Takes an optional `--brief` argument.

For each output schedule node of the current session, prints:

* A prominent banner with the schedule node's ID.

* The outputs of `view_all_commentary` for the schedule node,
  inheriting the `--brief` behavior.

Error if the current session has no output yet.


## Golden Object Tools

### New Golden Tool

    dh_hl new_golden -s ... {remarks file} [schedule ID]
<!-- help -->

Create a new golden object with remarks from a given file,
and the given schedule node.
The special value `none` encodes "no schedule node".

This will fail if the current session does not have an algorithm
`hlpipe` file already built for the given non-none schedule node,
using its 0th generator parameters object.

This is to prevent picking a golden schedule node that's impossible to satisfy.

<!-- We promised in "Golden Object State" to explain this in the new_golden help -->
The expectation is all generators will output a serialized `algorithm_hlpipe`,
which is the pipeline *before any scheduling directives are applied*.
The harness gives the output path for the serialization as the
`DENDRITIC_HL_ALGORITHM_HLPIPE` environment variable.
Insert the following between the algorithm definition and the scheduling:

    // Output algorithm as serialized pipeline, before any scheduling.
    if (const char* path = getenv("DENDRITIC_HL_ALGORITHM_HLPIPE")) {
        serialize_pipeline(Pipeline(std::vector<Func>{output}), path);
    }

Sometimes a Halide optimization is impossible to express
except through an algorithmic change.
The purpose of this feature is to quickly verify the typical case
where only scheduling changes are made.
New goldens will keep a record of when the algorithm changed,
which warrants additional scrutiny.
these decisions are between the user and the agent.

This is obviously vulnerable to reward hacking (don't do this).
The human programmer should manually review the final pipeline
before production usage to ensure the algorithm is as intended.
<!-- end help -->
<!-- impl -->

Note, we don't use the `-e hlpipe` generator option
since that would serialize the finished scheduled pipeline.
Trying to compare algorithm equality of the scheduled pipelines
is actually really really hard thanks to
wrappers, clones, `compute_with`, and especially `rfactor`.
<!-- end impl -->


### Golden History Tool

    dh_hl golden_history -C ...

Prints most recent to least recent golden objects, separated by dividers.
<!-- help -->
Each is printed as

* `timestamp: {timestamp}`

* `schedule: {schedule ID}` (short if possible), or `schedule: none`

* remarks
<!-- end help -->


### JSON Golden Info Tool

    dh_hl json_golden_info -C ... {golden ID}
<!-- help -->

Print info for the given golden object as a JSON object with key/value pairs

* `remarks`: string

* `schedule`: null, or string holding schedule node full ID
<!-- end help -->


## Problem Object Tools

### New Problem Tool

    dh_hl new_problem -C ... {short name} ...

Add a new problem with the given short name,
and problem CLI arguments as specified in the remaining arguments.
Gives an error in case an identical problem already exists,
and give the ID of the identical problem.
<!-- help -->
(Note, this is essential due to how problems are ID'd by hash).

The command line strings include some special values:

* `<RunGenMain>`, valid only as the 0th argument.
  If given, the harness will link the Halide pipeline
  into a standalone `RunGenMain` binary.
  Otherwise, you must supply a runner binary as the 0th argument,
  which must load the Halide pipeline from a shared library,
  and must provide a Halide runtime.

* `<Lib>`, valid only if `<RunGenMain>` is not used.
  Path to shared libary holding Halide pipeline,
  built with no Halide runtime.
  This path is also given through the `DENDRITIC_HL_OUTPUT_LIB` environment variable.

* All other `<...>` arguments are reserved and invalid.

There must be at least one argument.
Incorrect `<...>` arguments will be diagnosed immediately.

Prints the new problem's ID.
The default state is "enabled".

<!-- We promised to explain this here in "Problem Object State" -->
**Custom Runner Setup:**
If you don't use the default `<RunGenMain>` runner,
you must provide a runner binary that accepts a Halide pipeline
to test as a shared library.
This only has to be done once, then left alone in the agent hot loop.

1. **Include the generated header for the entry declarations.**
  Include the Halide-generated header to get the `extern "C"` declarations.
  Note the harness can *build* the Halide pipeline without a runner.
  Use `copy_build_output header`.
  Also `#include "HalideBuffer.h"` (header-only, no linking)
  to marshal inputs/outputs as `Halide::Runtime::Buffer<T>`.
  This is unchanged from the static-link world.

2. **Own the runtime in the runner.** Link one standalone runtime object
   (`halide_runtime.o` or `libHalideRuntime`) and install any custom
   handlers here (e.g. `halide_set_custom_do_par_for(...)`, error/print handlers).
   Because the pipeline `.so` is `no_runtime`, these apply to whatever pipeline is
   loaded.

3. **Resolve the entry point**, either:
   `void* h = dlopen(path, RTLD_NOW|RTLD_LOCAL);` then
   `auto fn = (int(*)(halide_buffer_t*, ..., halide_buffer_t*))dlsym(h,
   "dh_hl_pipeline");` cast to the header's prototype.

4. **Call it** with the buffers, exactly as a statically-linked call would.
   Use `reinterpret_cast` (not `static_cast`) for the `dlsym` result -- casting a
   `void*` to a function-pointer type with `static_cast` is ill-formed C++.
   <!-- Verified end-to-end by test_shared_lib_halide.py. -->

        void* h = dlopen(lib_path, RTLD_NOW | RTLD_LOCAL);
        if (!h) {
            ...
        }
        auto fn = reinterpret_cast<decltype(&dh_hl_pipeline)>(dlsym(h, "dh_hl_pipeline"));
        Halide::Runtime::Buffer<uint8_t> in(64, 64), out(64, 64);
        in.fill(100);
        int rc = fn(in, out);   // use whatever your real inputs/outputs are

5. **Build the runner once** (`-Wl,-export_dynamic` on macOS / `-rdynamic` on
   Linux, include `-I$HALIDE/include -I$HALIDE/../src/runtime -I.`,
   link the runtime object, `-lpthread -ldl`).
<!-- end help -->


### Problem State Tools

    dh_hl disable_problem -C ... {problem ID}
    dh_hl enable_problem -C ... {problem ID}
    dh_hl set_main_problem -C ... {problem ID}

Respectively,

* Set problem state to `disabled`.

* Set problem state to `enabled`, unless its state was `main`.

* Set problem state to `main`,
  and set all other problems with `main` state to `enabled` state.


### Problem Short Name Tools

    dh_hl get_problem_short_name -C ... {problem ID}
    dh_hl set_problem_short_name -C ... {problem ID} {short name}


### List Problems Tool

    dh_hl list_enabled_problems -C ...
    dh_hl list_all_problems -C ...
<!-- help -->

List all enabled problems or all problems, respectively.
Each is printed separated by dividers.

Each problem is printed as four lines:

* `id: {id}` (short ID if possible)

* `state: {state}`

* `short name: {short name}`

* `cli: {CLI args, as one-line JSON list}`
<!-- end help -->


### JSON Problem Info Tool

    dh_hl json_problem_info -C ... {problem ID}
<!-- help -->

Print info for the given problem object as a JSON object with key/value pairs

* `argv`: list of strings, command line arguments

* `state`: string

* `short_name`: string
<!-- end help -->


## Other Session Tools

### Set Idea Tool

    dh_hl set_idea -s ... {idea ID}

Updates the current idea state to "some current idea",
embedding the given idea node ID.
It is an error if the ID doesn't resolve to a single existing idea node.

This leaves the workspace C++ file and generator parameters alone.
To reset both the workspace files and the current idea state,
consider `restore_schedule` or `restore_idea`.


### List Sessions Tools

    dh_hl list_open_sessions -C ...
    dh_hl list_termini -C ...

List all open session nodes or all termini ("terminuses") of the current catalog.
Give both full session IDs and session handles.


### Should-accept Schedule Tool

    dh_hl should_accept -s ... [schedule ID]

Check the given schedule's suitability to be the primary output schedule.
This tool gives the check-override flags that need to be passed to
`dh_hl close_session` to force the primary output schedule anyway.
<!-- help -->

**Failed Problem Check:**
Run for all sessions.

For each enabled problem and each generator parameters of the given schedule,
search for a benchmark sub-object reachable from the private benchmark set list
that encodes the given schedule, generator parameters, and problem.
(recall failed benchmarks runs don't emit benchmark objects).

The check fails if any benchmark search failed.
The `close_session --allow-failed-problems` flag overrides this.
This is almost certainly a bad idea,
but the harness allows it in case of unforseen circumstances.

**Failed Golden Check:**
Only run for top-level sessions.

If the golden schedule node exists,
check for binary equality between its `algorithm_hlpipe`
and the given schedule node's `algorithm_hlpipe`.
The check fails if they are not equal, or they don't exist.

If either `algorithm_hlpipe` doesn't exist, the tool suggests
`init_build --target {schedule ID} --other golden` and `build`.
This could still fail because the generators didn't emit the
`algorithm_hlpipe`.
The tool will keep recommending a rebuild in this case,
but you have to actually fix the generator for it to work.

The `close_session --allow-failed-golden` flag overrides this.
This is also a bad check to override.
If the goldens don't match AND you have very good reason to think
the end user will approve of this algorithm deviation,
create a new golden to record this decision.

**Deleted Problem Check:**
Only run for top-level sessions.

If any of the session's enabled problems on opening are now disabled,
the check fails.

The `close_session --allow-disabled-problems` flag overrides this.
Disabling problems is like deleting test cases.
There should be a good reason for this.
Usually disabling the `default` problem is acceptable;
it's just there for convenience.

**Changed Golden Check:**
Only run for top-level sessions.

If the session's golden schedule node on opening exists,
and it's not the same as the current golden schedule node
(either a different node or doesn't exist anymore),
the check fails.

The `close_session --allow-changed-golden` flag overrides this.
Don't overide this check unless you actually have a very good reason
to think the end user will approve of this algorithm change.
<!-- end help -->


### Close Session Tool

    dh_hl close_session -s ... [schedule IDs...]

Add outputs to the current session, making it self-closed.
This promotes a fair amount of private (not git tracked)
session state to public (git tracked) session node state.
This accepts the check-override flags given by `dh_hl should_accept`.
<!-- help -->

**Output Schedules:**
The `[schedule IDs...]` is a list of schedule node IDs
(each ID is a separate `argv` argument);
these are added as the output schedules of the current session.
An empty list behaves like the default `[schedule ID]` argument.
Recall the first one given becomes the primary output schedule.

Each output schedule's pool tag is the pool tag of its parent idea,
as defined by the current session's private idea list.
It's an error if any root nodes are given,
or if the parent idea is not in the private idea list
(fix with `dh_hl set_pool_tag`).

All output schedules must be major schedules and must have commentary
(the tool will remind of the `comment` tool in the latter case).

The primary output schedule is subjected to `dh_hl should_accept` checks.
If `dh_hl should_accept` requires some override flags,
and those were not provided,
the tool errors and the session is *not* self-closed.

**Output Benchmark Sets:**
Same as the current session's private benchmark set list.

**Added superseded-by Links:**
For each output schedule `O`, find the schedule node `R`
that would be found by `dh_hl session_root_of O`.
Add a superseded-by idea side link from `R`'s parent to `O`'s parent.
This step is silently skipped for output schedules
where `session_root_of` would fail.
<!-- end help -->


### Join Session Tool

    # Acquires session lock of the current session only.
    dh_hl join_session -s {current session handle/ID} {joined session handle/ID}

Adds joined session outputs to the current session's private idea list
and private benchmark sets; error if the joined session lacks outputs.
Accepts `--dry-run` (print only) and `--pool-prefix {pool prefix}` arguments.
The default `pool prefix` is an empty string.
<!-- help -->

If `--dry-run` is not given,

* Add all benchmark sets from the joined session's output to the
  current session's private benchmark set list.

* For each joined session's output schedule node,
  add its parent idea node to the current idea list,
  with the assigned pool tag being:

  * the existing pool tag unchanged, if the idea was already in the list

  * the output schedule node's pool tag otherwise,
    prefixed with `{pool prefix}.` if the pool prefix is non-empty.

Whether or not `--dry-run` was given, this prints out

* The benchmark sets (that would be) added,
  each on a line of the form `dh_hl: add benchmark set {id}`

* The idea nodes (that would be) added, as two lines of the form
  `dh_hl: add idea {id}`, `dh_hl: pool tag {pool tag}`.
  The pool tag given includes the added prefix.

These are designed to be greppable,
in case the agent wants to undo some of this later.
<!-- end help -->
<!-- impl -->

An unfriendly raw Python exception is acceptable for the root-schedule case
(a root output) since `close_session` forbids it, so it can't happen without
manually corrupting the catalog files.
<!-- end impl -->


### Delist Session Tool

    dh_hl delist_session -s ...
<!-- help -->

Set the is-delisted flag of the current session to true.
Useful to get rid of old abandoned sessions in the open sessions or termini list.
<!-- end help -->


### View Session Prompt Tool

    # Does not acquire session lock
    dh_hl view_session_prompt -s ...
<!-- help -->

Prints the plain text prompt of the current session,
followed by `=== Seed Ideas ===`,
followed by the output of the `list_seed_ideas` tool.
<!-- end help -->


### JSON Session Info Tool

    # Does not acquire session lock
    dh_hl json_session_info -s ...
<!-- help -->

Prints the state of the current session as a JSON object, with key/value pairs

* `id`: full ID of node

* `parent`: string or null, full ID of parent session

* `children`: list of strings, each a full ID of a session node

* `prompt`: string

* `default_anchor_schedule`: string or null,
  full ID of default anchor schedule node

* `golden_schedule_on_opening`: string or null,
  full ID of the golden schedule node when the session was created

* `enabled_problems_on_opening`: list of strings,
  full IDs of the problems enabled when the session was created

* `seed_ideas`: list of strings, full ID of seed idea nodes

* `output_schedules`: list of strings or null,
  full ID of output schedule nodes

* `output_benchmark_sets`: list of strings or null,
  full IDs of output benchmark sets

* `delisted`: bool

* `depth`: number
<!-- end help -->


## Other Idea Node and Schedule Node Tools

### New Idea Tool

    dh_hl new_idea -s ... {proposal name} {proposal file} [schedule ID]

Adds a new child idea node to the referenced schedule node,
which must be a major schedule.
Furthermore, the idea node is added to the current session's
private idea list.
<!-- help -->
The pool tag is:

* given by `--pool-tag {pool tag}` if this argument is given

* otherwise, the pool tag of the referenced schedule node's parent.
  This fails (`--pool-tag` required) if either the referenced schedule node
  is a root node, or if its parent idea node is not in the private idea list.

It is an error if this would cause an ID collision
(i.e. the proposal name is already used).

Gives back the ID of the new idea node.

<!-- end help -->
<!-- impl -->
If the schedule node is a minor schedule, the tool advises:
* If its parent idea node already has a canonical schedule,
  give its ID and advise passing it explicitly to the `new_idea` tool
* If its parent idea node has no canonical schedule,
  advise `dh_hl canon` tool is appropriate if the current schedule builds
  and you are happy it correctly implements the idea.
* (no other cases: minor schedules are not root nodes by definition)
<!-- end impl -->


### New Root Tool

    dh_hl new_root -s ...

Create new root schedule node from workspace files.
Generally this should be avoided.
<!-- help -->

Hashes the workspace files and looks for existing schedule nodes with the same hash.
If any of them are major schedules, the tool errors,
giving IDs of all such schedule nodes.

Otherwise, it creates a new root schedule node containing a
copy of the workspace file, and sets the current idea state to
"no current idea", embedding the timestamp of the new schedule node.

This tool succeeds regardless of the contents of the current idea
state on disk. If parsing the existing file yields multiple
encoded current idea states, it additionally adds
commentary to the new schedule node of the form:

        dh_hl new_root tool: automated merge conflict recovery
        [one line for each encoded current idea state parsed,
        in any order and the same format as the current idea state file]

and with the default `neutral` review.
This is just a temporary "bare minimum" merge conflict resolution.

FUTURE: probably remove this extra merge conflict recovery functionality later.
<!-- end help -->


### Canon Tool (Make Canonical Tool)

    dh_hl canon -s ...

Sets the canonical schedule of the current idea node to the schedule node named by `dh_hl status`.
This is a schedule node that holds a copy of the workspace schedule.

Requirements:
    * Current idea node must exist
    * `dh_hl status` would give an unambiguous schedule node ID
    * Referenced schedule must have a `success` result state.
    * The current idea node must not already have a canonical schedule.

There is intentionally no "change canonical schedule" tool.
<!-- impl -->

If the command fails due to exiting canonical schedules:
    * If the schedule node is already the canonical schedule, the tool notes it was already done.
    * Otherwise, it advises the `dh_hl new_idea ... {canonical ID}` and `dh_hl set_idea` tools,
      where the `{canonical ID}` is the ID of the major schedule that blocked this command.
      The advice (shared with `init_build` via `catalog.canonical_block_advice`)
      puts the schedule ID last, matching `new_idea`'s `<name> <proposal file> [schedule]` order.
<!-- end impl -->


### List Ideas Tools

    # All commands do not acquire session lock
    dh_hl list_child_ideas -C ... [schedule ID]
    dh_hl list_seed_ideas -s ...
<!-- help -->

`list_child_ideas` prints a summary of each child idea node of
the referenced *major schedule* node; error if passed a minor schedule.

`list_seed_ideas` prints a summary of each seed idea of the current session.

Prints these lines for each idea node:

* The ID of the idea node (all lines except this one indented by 2)

* ID of canonical schedule, or `(none)`, prefixed by `canonical: `

* The proposal name, prefixed by `proposal: `

* For `list_child_ideas` only,
  the first up-to 72 characters of the first line of the proposal text.

* For `list_child_ideas` only,
  if the last non-empty line of the proposal text starts with
  `Created for session:`, print that line.
  (See "Session Creation Tools: Common Information").

* For each idea side link,
  print `borrowed from: {idea short ID}`
  or `superseded by: {idea short ID}` as appropriate.
<!-- end help -->


### View Idea Tools

    # All commands do not acquire session lock
    dh_hl view_idea -C ... {idea ID}

Prints the referenced idea node's

* proposal name

* full proposal text

* list of child schedule IDs, one line each

* idea side links, in the same format as `list_child_ideas`


### Add Idea Side Link Tool

    # Reads like a sentence, e.g. abcdef.foo borrows_from 123456.bar
    dh_hl add_idea_side_link -C ... {idea ID lhs} {type} {idea ID rhs}

Add an idea side link from the LHS idea to the RHS idea,
of type `borrows_from` or `superseded_by`.
Silent no-op if this exactly duplicates an existing idea side link.
(i.e. same LHS, RHS, and type).


### History Tool

    dh_hl history -C ... [schedule ID]

Walks the branch of the tree from the referenced schedule node
up toward a root node.
<!-- help -->
For each schedule node, prints:

* Its ID
* Its child idea nodes in the same format as `dh_hl list_child_ideas`,
  marking the child idea node that is the parent of the previously printed schedule node.
* For each commentary file, its timestamp on one line,
  and the first up-to-72 characters of the first line of the commentary text.
<!-- end help -->


### List Schedules Tools

    # Does not acquire session lock
    dh_hl list_output_schedules -s ...
    dh_hl list_sibling_schedules -C ... [schedule ID]
    dh_hl list_child_schedules -C ... {idea ID}
    dh_hl list_equal_schedules -C ... [schedule ID]
<!-- help -->

Lists all schedule nodes matching some criterion:

* `list_output_schedules`:
  list all output schedules of the current session.
  Error if the current session has no outputs yet.

* `list_sibling_schedules`:
  list all schedule nodes that have the same parent as the given schedule.
  Error if a root node is given.

* `list_child_schedules`:
  list all children of the given idea node.
  (This partially overlaps the `view_idea` tool, with different verbosity).

* `list_equal_schedules`:
  list all schedule nodes with the same hash as the given schedule.

Each schedule is printed in the same manner as `dh_hl history`
(ignoring the "marking the child idea node" part),
with a clear separator between each.
There is no predefined order of the schedules,
except for `list_output_schedules` (ordered as stored in session output).
<!-- end help -->


### Root Query Tools

    # Does not acquire session lock
    dh_hl root_of -C ... [schedule ID]
    dh_hl session_root_of -s ... [schedule ID]

Starts at the referenced schedule node and starts walking the tree
towards the root.
For each schedule node,

* for `root_of`, print the ID of the node and exit if it's a root node.

* for `session_root_of`, print the ID of the node and exit if its parent
  idea exists and is a child of a seed idea of the current session.

The `session_root_of` search may fail.
The `root_of` search won't fail for a non-corrupt catalog.


### Force Parent Idea Tool

    dh_hl force_parent_idea -C ... {idea ID} [schedule ID]

Rarely needed, mostly for when a new root node was created and you regret it.
<!-- help -->

Adds the referenced schedule node as a child and the canonical
schedule of the referenced idea node.

This fails if:
* The referenced schedule node is not a root node.
* The referenced idea node already has a canonical schedule.
* The new edge would cause a tree structure invariant violation.
<!-- end help -->


### Copy Schedule Command

    dh_hl copy_schedule -C ... {output file} [schedule ID]

IMPL TASK: huge number of tools eliminated thanks to new `[schedule ID]` magic arguments.

IMPL TASK: `--parameters`

Copy the referenced schedule node's C++ generator to the given file.
The optional `--parameters` value causes the
`generator_parameters.json` to be copied instead.

See also `restore_schedule`.


### View Generator Parameters Tool

    dh_hl view_generator_parameters -C ... [schedule ID]

Pretty-print the `generator_parameters.json` stored in the named schedule node.
Each generator parameters object is printed as a single line

    [0-based index] [JSON object as one line]


### Fix Canonical Tool

    dh_hl fix_canonical -C ... {idea ID}

Fix competing idea node canonical schedule after merge conflict.
<!-- help -->

After a merge conflict, the referenced idea node's canonical schedule may
record two competing IDs. This tool resolves that by modifying the catalog
graph so that:

* The older canonical schedule becomes the canonical schedule
  of the referenced idea node.
* That canonical schedule gains a new child idea node whose
  canonical schedule is the newer of the two competing schedules.
* The new child idea node has proposal name `fix_canonical_{timestamp}`
  and a proposal text noting it was auto-generated by `fix_canonical`.

**Warning:** this tool is AFAIK the only case where a
short ID can silently *change meaning*
(as opposed to become ambiguous, with a diagnostic).
However, this tool should be very rarely used,
only needed for dealing with regrettable git merges.
<!-- end help -->


### JSON Schedule Info Tool

    dh_hl json_schedule_info -C ... [schedule ID]
<!-- help -->

Prints the state of the referenced schedule node as a JSON object, with key/value pairs

* `id`: full ID of node

* `parent`: string or null, full ID of parent idea node if it exists

* `children`: list of strings, each a full ID of a child node

* `source`: string, C++ source code

* `parameters`: list of generator parameters JSON objects stored in the node

* `timestamp`: string, timestamp

* `hash`: string

* `result`: string, `result.txt` result

* `benchmark`: list of child benchmark sub-objects

* `review`: string value derived from commentary

* `commentary`: list of objects, with key/value pairs:
    * `id`: full ID of commentary

    * `text`: full commentary text

    * `review`: string review value

    * `cancels`: cancels list as list of commentary full ID strings

    * `cancelled_by`: unordered list of commentary sub-objects (by full ID)
      that contain this commentary sub-object in their cancels list.
      This commentary is cancelled iff the `cancelled_by` list is non-empty.

* `warning_toggles`: list of `WarningToggle` sub-objects,
  each object with key/value pairs:
    * `id`: full ID of `WarningToggle` sub-object

    * `citation`: full ID of commentary

    * `func`: string name of blocked warnings' function name; null if not applicable

    * `rule`: string name of blocked warnings' rule; null if not applicable

    * `cancels`: full ID of `WarningToggle` object; null if not applicable
<!-- end help -->


### JSON Idea Info Tool

    dh_hl json_idea_info -C ... {idea ID}
<!-- help -->

Prints the state of the referenced idea node as a JSON object, with key/value pairs

* `id`: full ID of node

* `parent`: string, full ID of parent schedule

* `children`: list of strings, each a full ID of a child node

* `proposal_name`: string

* `proposal_text`: string

* `canonical_schedule`: null if no canonical schedule, otherwise string full ID of the canonical schedule

* `idea_side_links`: list of objects in unspecified order;
  each unique link starting from this idea node is in the list exactly once,
  with key/values `id: string` (destination of link) and `type: string`.

* `review`: string value derived from commentary
<!-- end help -->


## Misc Tools

### Locked Execution Tools

    dh_hl exec --
    dh_hl exec_exclusive --

Execute a CLI command with the machine lock held,
in concurrent mode for `exec` and exclusive mode for `exec_exclusive`.
This is necessary for executing non-harness commands without interfering with other agents' profiling.

The executed command is formed from all the arguments passed after `--`.
The N-th argument after `--` is the N-th `argv` value for the CLI command,
e.g. the following prints a single file `hello world.txt` with the exclusive lock held:

    dh_hl exec_exclusive -s tmp.abc123 -- cat "hello world.txt"

The `-s` session argument is not needed, but is accepted here for consistency.


### Catalog Location Tool

    dh_hl catalog_location -C ...
<!-- help -->

Print the path to the catalog directory.
Non-trivial when the `-s {session handle}` option is used.
<!-- end help -->


### ID Translation Tools

    # All commands do not acquire session lock
    dh_hl schedule_full_id -C ... [schedule ID]
    dh_hl schedule_short_id -C ... [schedule ID]
    dh_hl idea_full_id -C ... {idea ID}
    dh_hl idea_short_id -C ... {idea ID}
    dh_hl session_full_id -s ...
    dh_hl session_handle -s ...
    dh_hl benchmark_full_id -s ... {benchmark ID}  # No benchmark_short_id
    dh_hl commentary_full_id -C ... {commentary ID}
    dh_hl commentary_short_id -C ... {commentary ID}
    dh_hl WarningToggle_full_id -C ... {WarningToggle ID}
    dh_hl WarningToggle_short_id -C ... {WarningToggle ID}
    dh_hl problem_full_id -C ... {problem ID}
    dh_hl problem_short_id -C ... {problem ID}

IMPL TASK: add new ones

On success: print out the ID/handle with a newline, and no other `stdout` output.

Short ID getters may silently fall back to giving a full ID.
However, the `session_handle` getter will never give back a session full ID:
this is load bearing for correctness, since it encodes more than a session full ID
(namely, the catalog directory location).


### JSON Export Entire Catalog Tool

    dh_hl json_export -C ...
<!-- help -->

Exports the entire catalog as a JSON object, with key/value pairs

* `ideas`: idea nodes

* `schedules`: schedule nodes

* `sessions`: session nodes

* `benchmark_sets`: benchmark set objects

* `goldens`: golden objects

* `problems`: problem objects

Each value is itself an object, with keys being string full ID and values
being JSON objects in the same format as `json_schedule_info`,
`json_idea_info`, `json_session_info`, `json_benchmark_set_info`,
`json_golden_info`, `json_problem_info`.

FUTURE: holds the exclusive catalog lock despite being conceptually read-only.
Optimize this if needed, but this shouldn't be in the agent hot loop.
<!-- end help -->
<!-- main -->


# Main Agent Default Session Behavior

This step gives reasonable defaults, which take second priority to the
user's instructions or more authoritative prompts.

If the user provided an existing C++ file, suggest the `new_catalog`
tool with a reasonable directory location, and execute the tool if approved.

If the user provided an existing `*.dh_hl` catalog,
inspect it with the `list_termini` tool.
If there's exactly one terminus, adopt it as your current session
and inspect it with `dh_hl status`.

* If the terminus is closed, use the `new_successor_session` tool,
  and adopt the successor as your current session with `init_workspace`.
  Add a reasonable proposal (prompt for yourself) if you have more specific
  goals for the session, or just write a generic description if not.

* Otherwise, use the `init_workspace` tool and start work on the
  existing terminus.
  Unless advised otherwise, don't use `--force`,
  and do follow any warnings given by the `init_workspace` tool.

If none of these cases (e.g. multiple termini),
then the user needs to provide more specific intentions.
The user may not be familiar with the harness.
Try to advise of the implications of various actions.
<!-- end main -->
