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


## Conceptual State

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

Finally, the `build` (profiler) tool creates **benchmark set objects**
that group "batches" of benchmarks across different schedule nodes.
This is for comparison tools, which only compare within batches to fight noise.

FUTURE: comparison tool not yet specified. Ignore `three_way_bench.md`
and other files not referenced transitively by `idea.md` (this file).


### Schedule Node Terminology

A schedule node is a **root node** if it has 0 parents.
The "tree" is technically a forest as multiple roots are possible.

A schedule node is a **major schedule** if it is a root node or it is a canonical schedule of some idea node.

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


### Schedule Node State

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
  * `runtime error`: All generator parameters led to successful Halide binary
    generation, but not known *yet* that all binaries execute successfully.
  * `success`: All Halide binaries known to run successfully (best).

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


### Idea Node State

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


### Session Node State

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

* **Default Anchor Schedule:** Optional reference to schedule node.

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

**Session Golden Rule:** two concurrent agents must never have the same
current session, unless the tool is marked as an exception (`does not acquire session lock`)
The session lock (see "Locking") will catch many such violations,
but will not prevent observing a partial edit to the workspace C++ file.


### Commentary Sub-object State

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


### WarningToggle Sub-object State

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


### Benchmark Sub-object State

**Benchmark Full ID:** `{parent schedule node full ID}_{hostname}_{timestamp}`.

JSON object with key value pairs:

* `hostname`: string, not sanitized

* `cpu_count`: number, CPU count of system used for profiling

* `timestamp`

* `parameters`: object, generator parameters used to generate the profiled Halide binary

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


### Benchmark Set State

**Benchmark Set Full ID:** `{sanitized hostname}_{timestamp}`

<!-- deferred task: strip when creating prompt -->
Rationale: timestamp alone would be reasonable for one machine
(would uniquify even on collisions due to minted timestamp behavior).
Minting will fail if we parallelize across multiple machines.
Using the computer name to unique-ify makes sense since it's unreasonable
to expect comparing profiler runs on different machines to make sense.
(I hope I don't live to eat these words -- but breaking changes are AOK for now).
<!-- end strip -->

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


### Current Idea State

We need to keep track of which idea the schedule in the workspace should be parented to.
The "current idea state" stored in the current session is a tagged union of

* **No current idea state**: contains a timestamp;
  indicates the workspace schedule is to become a root node.

* **Some current idea state**: contains full ID of an idea node.


### Current Anchor Schedule

Each session may reference a single "anchor schedule", or none at all,
as part of its session private workspace state.
Wall time costs are based on relative comparison to the anchor schedule,
except for direct 2-way comparisons.


### Private Idea List

List of idea nodes stored in session private workspace state.
Each is associated with a string "pool tag" and a cost.

FUTURE: This is used to build a "frontier" of ideas for the
session's agent to explore (`list_private_ideas` tool).


### Private Benchmark Set List

List of benchmark sets stored in session private workspace state.
These are the benchmarks considered for schedule cost and comparison.

FUTURE: actually use this, but for now this state already has to
be implemented because of `dh_hl join_session`.

FUTURE: explicit manipulation of the list.


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


### Terminus Schedule ("Final Result")

IMPL TASK: now defined as primary output schedule

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


## Full and Short IDs

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

* `{schedule ID}.{hostname}_{timestamp}`
  Find all schedule nodes matching the given ID (long or short),
  then match any benchmark sub-objects of those schedule nodes
  that have a matching (sanitized) hostname and timestamp.

**WarningToggle short ID:**

* `{schedule ID}.{timestamp}`
  Find all schedule nodes matching the given ID (long or short),
  then match any `WarningToggle` sub-objects of those schedule nodes
  that have a matching timestamp.

**Warning:** short IDs may become invalid due to new ambiguities.
Use them only as convenient IDs for immediate tool use,
and not long-term identification (e.g. in commentary text).

Currently benchmark sets don't have short IDs.


## Session Handles

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


## The Machine Directory

This directory stores the machine lock (for profiling)
and the state needed for session handle translation.

It is in `~/.cache/dendritic_hl/`,
with the `~/.cache` portion overridable with the `XDG_CACHE_HOME` environment variable.


### Locking

All tools acquire the **machine lock**, usually concurrently.
The profiling step of `build` and any `exec_exclusive` command
acquire the machine lock exclusively.

All tools that access a catalog acquire an exclusive per-catalog **catalog lock**.

All tools that require a current session (`-s`) acquire an exclusive per-session **session lock**,
except for a subset of read-only commands, marked when their syntax is introduced.
This locking *never* fails for correct usage: failure to acquire is diagnosed as an error
(two concurrent agents using the same session).

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


<!--
deferred task: move tools to new cli.md.
I decided to stop storing tool implementation details in impl.md
since it was getting annoying how each tool was documented in two places.
Source help text from cli.md instead of idea.md with "impl" sections removed.
Future skill will get an even smaller version of cli.md, as idea.md is too verbose now.
-->

<!--
  FORMAT CONTRACT for the code (main.py `_parse_idea_sections`): `dh_hl help`
  renders its docs from this "## Tools" section, so keep the shape:
  * The prose between this "## Tools" heading and the first "###" below is
    printed verbatim by `dh_hl help` (no argument) as the common usage notes.
  * Each tool is a "### <Name> Tool(s)" heading whose FIRST indented block is a
    synopsis with one "dh_hl <command> ..." line per command it documents;
    `dh_hl help <command>` locates the section by that command and prints it.
    One section may document several commands (they render together).
  * "NOTE: [link ...]" lines and HTML comment lines are stripped from the
    rendered help output.
-->
## Tools

The tools are invoked with `dh_hl {tool name} args...`.
There are two frequest arguments:

* `-C`, `--catalog`: gives the directory name (must end with `.dh_hl`) for the current catalog.

* `-s`, `--session`: gives the session node full ID OR a session handle.
  A session handle may substitute for a mandatory `-C` argument;
  if both are given, the catalog directory must match.

Tools that *require* a current session have `-s` shown as an explicit argument,
but note `-C` is implicitly required if `-s` passed a session node full ID.
Tools that *require* only a catalog directory have `-C` shown as an explicit argument.
However, all tools accept both arguments, for simplicity.

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

The "current idea node" is nothing, if the current idea state encodes "no current idea",
and otherwise the idea node referenced by the "some current idea" state.
Commands that explicitly edit the current idea state must not error out
due to errors in the existing `current_idea_state.txt`.

Tools that print an ID or such with no other output still
include a trailing newline, as customary for Unix tools.


### Help Tool

    dh_hl help [command]

With no `[command]`, lists all commands briefly; with a `[command]`, describes that one.


### Help Tool — Implementation Details
<!-- deferred task: strip when creating prompt -->

Both help views render from **this repo's `idea.md`** (the single source), via
`main._parse_idea_sections()`, which returns `(intro, mapping)`:

* `dh_hl help` (no arg) lists the `COMMAND_HELP` one-liners, then prints the
  `intro` — the prose between the "## Tools" heading and the first "###" tool
  section (the shared argument conventions).
* `dh_hl help <command>` prints `mapping[command]` — the detailed "### ... Tool"
  section, keyed by the commands in each section's leading indented `dh_hl <cmd>`
  synopsis block (not by heading name), so a multi-command section like "Copy
  Schedule, ID-of Schedule Tools" maps all its commands to the same shared text.

Maintainer-only lines (`NOTE: [link…]`, `<!-- … -->`) are stripped from both.
The format `_parse_idea_sections` relies on is spelled out in a FORMAT CONTRACT
comment just above "## Tools" in `idea.md`.  `idea.md` lives one level above the
package dir, so a copy run detached from the repo won't find it — `help` then
degrades to the command list / one-liner (no crash).

Doc/code stay bound by a test (`tests/test_help.py`) asserting the CLI command
set equals the set of commands `_parse_idea_help()` finds — add a command
without an idea.md tool section (or vice versa) and it fails.


### Prompt Tools

    dh_hl prompt --main
    dh_hl prompt --sub
    dh_hl detail name
    dh_hl examples name

The `prompt` tool prints the standing agent prompt,
for either the main-agent (`--main`) or sub-agent (`--sub`) audience.
Exactly one of `--main`/`--sub` is required.
The audience is deliberately **not** inferred from the current session,
so the prompt can double-check that the agent is running with the role it thinks
it is (e.g. that a spawned sub-agent wasn't handed a main-agent's session).

The prompt mentions supplemental documents in the `detail/` or
`examples/` directory, which are part of the harness source repo.
The `detail` and `examples` tools fetch a named file from those
respective directories and prints it to `stdout`.

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


### Prompt Tools — Implementation Details
<!-- deferred task: strip when creating prompt -->

    dh_hl prompt --main
    dh_hl prompt --sub
    dh_hl detail name
    dh_hl examples name

All three live in `prompts.py` (the assembly logic) with thin `cmd_*` wrappers
in `tools.py`; none needs a catalog or session (they read the harness *source*
repo, one level above the package dir, via `prompts._REPO_DIR`).

`prompt` (`prompts.load_prompt`) concatenates four processed docs, in order,
separated by a single blank line:

* `prompt_common.md`, with main/sub-agent specialization applied AND HTML
  comments removed (`parse_prompt`, below).

* `idea.md`, with HTML comments removed

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

**Main/sub-agent specialization:** `prompt_common.md` content is
COMMON unless wrapped in an audience *fence* — an HTML comment whose
only word is `main`/`sub`, closed by `end main`/`end sub`.

`parse_prompt(text, audience)` emits common lines plus
matching-audience lines, dropping the other audience's fenced regions, fence
lines, and all HTML comments (the format-contract comment included); it then
collapses the blank runs so the output reads cleanly.

The audience is **explicit only** — never inferred from the session — so the
prompt can double-check the agent's role (e.g. catch a sub-agent that was handed
a main session).  argparse makes `--main`/`--sub` a required mutually-exclusive
pair.

`parse_prompt` is the format guard: it raises `DhHlError` on nesting, an
unmatched/dangling fence, or a fence-shaped comment naming a non-`main`/`sub`
audience (single-word comments are reserved for fences, so a typo fails loudly
rather than silently leaking a region into both prompts).  The rules are spelled
out in a FORMAT CONTRACT comment atop `prompt_common.md`.  Like idea.md, the file
sits above the package dir; if missing, `prompt` errors cleanly (no fallback —
the prompt has no default content).  Covered by `tests/test_prompt.py`.


### Status Tool

    # Does not acquire session lock
    dh_hl status -s ...

This is a purely read-only command.
<!-- Agents used to have to run this on startup,
but the safety this provided got replaced with init_workspace. -->

If there was no current session given, the tool errors.
Otherwise, the tool tries to find a schedule node that already holds
a copy of the workspace files,
and give basic information on the current catalog state.

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

**Search Implementation Details:**  <!-- deferred task: strip when creating prompt -->

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

**Output Implementation Details:**  <!-- deferred task: strip when creating prompt -->

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


### Restore Schedule Tool

    dh_hl restore_schedule -s ... {schedule ID}

Copies the schedule node's C++ schedule and generator parameters to
the workspace, and updates the current idea state as follows,
depending on the number of parent idea nodes of the referenced
schedule node.

* **No parents:** set to "no current idea" state, embedding the timestamp of the schedule node.

* **One parent:** set to "some current idea" state, embedding the ID of the parent idea node.


### Restore Idea Tool

    dh_hl restore_idea -s ... {idea ID}

Copies the idea's parent schedule's C++ code and
generator parameters to the workspace,
and updates the current idea state to "some current idea" state,
embedding the idea referenced in the command.

This restores the private workspace to a state where you are ready
to begin *implementing* the idea.
Note; the workspace will probably be inconsistent according to
`dh_hl status` after this command. This is normal.

This tool gives a warning if the referenced idea already has a canonical schedule.
The warning includes

* The ID of the canonical schedule
* A suggestion to use `restore_schedule` instead.


### Init-Build Tool

    dh_hl init_build -s ...

Prepare for an up-to-3-way comparison between Halide schedules,
including possibly a new schedule node made from current workspace files.

Takes optional arguments specifying the up to three schedule nodes.

* `--target {schedule ID}` selects the target schedule node.
  The special value `workspace` is the default, detailed below.

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

`--target workspace` behavior (a missing `generator_parameters.json` is an
error here, just as in `dh_hl status`):

* If `dh_hl status` would give an unambiguous schedule node,
  the target schedule node is the one this tool returns.

* Otherwise, if there is no current idea node for the session,
  give an error, and suggest the `set_idea` and `new_root` tools.

* Otherwise, add a new child schedule node to the current idea node
  holding a copy of the workspace files. This is the target node.

This is the main mechanism by which new schedules enter the catalog.
The harness (by design) can only build or profile schedules in the catalog.
So this is the first step to building or profiling a new schedule.


### Build Tool

    dh_hl build -s ...

IMPL TASK: private benchmark set list

Builds the schedule nodes selected by the latest `dh_hl init_build`
done with the current session (state stored in the session private workspace).
Optionally profiles them, generating new benchmark or benchmark set objects.
Generated benchmark sets are added to the private benchmark set list.

By default, if a schedule has `N`-many generator parameters objects,
then `N` binaries are built, one for each parameters object.
It's an error if any schedule node being built has 0 parameters objects.

The tool

1. Compiles the schedules selected by `init_build` into Halide binaries
   in the session private workspace `bin` directory, along with
   `.stmt` and `.conceptual.stmt` files for the target schedule.
   Compiler and generator outputs get piped to harness `stdout`/`stderr`.

2. (`--profile` only) runs all generated binaries with Andrew Adams's profiler,
   with new benchmark objects added to the profiled code's source schedule node.
   The new objects' IDs are printed.

3. Updates the result state of each built/profiled schedule node,
   monotonically (worse to better only).
   A `--only [int]` build caps the achievable result at `halide error`,
   since not all possible generator parameters are verified.
  <!-- Note: see separate pseudocode (stripped from dh_hl help) -->
  <!-- Note: --only 0 when only 1 parameters object exists breaks the -->
  <!-- above rationale, but we don't bother with this special case. -->

Flags:

* `--profile [N]` (`N = 0` default, must be a non-negative integer).
  This enables `N` batches of profiler runs.
  Each batch runs all generated binaries in a random order ("interleaved")
  Note, without profiling, `runtime error` is the best result possible.

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
A new benchmark set is generated, containing all benchmark objects
made by this tool run, iff `--only all` is in effect,
profiler batch count is at least 1, and no subprocesses failed.

Important lines emitted by the harness itself are prefixed with
`dh_hl: ` for grep-ability.


### Build Tool Implementation Details

<!-- deferred task: strip when creating prompt -->

It's crucial that the catalog lock is not acquired during the
compilation phase. This prevents locking out other agents
needlessly (despite they will be locked out soon by profiling).

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
                # Binary in session private workspace: bin/{node.full_id}_{i}
                # Also, for target node only, if generation succeeds, generate
                # bin/{i}.stmt, bin/{i}.conceptual_stmt
                # and print each path with "dh_hl: " lines
                # NB skipping ID prefix for .stmt for my human taste,
                # since no tool reads .stmt for me and I don't want to copy
                # a huge session ID + schedule node ID path. Re-eval later.
                print "dh_hl: end Halide generator {i} (success|fail)"

    # 2. Profiling
    if --profile N with N == 0:
        acquire_exclusive(catalog_lock)
    else:
        acquire_exclusive(machine_lock)  # Upgrade from concurrent
        acquire_exclusive(catalog_lock)
        binaries = []
        for node in nodes:
            for params in node.generator_parameters:
                if Halide generator succeeded:
                    binaries.append(...)
        for batch in range(N):
            shuffle(binaries)  # Shuffled each time
            for bin in binaries:
                profile(bin)
                node, params_index = source_of(binary)
                print "dh_hl: Profiled {node.short_id}, binary {params_index} (success|fail)"
                if success:
                    Add benchmark sub-object to binary's source schedule node
                    Timestamp could be taken before or after profiling, unimportant
                    print "dh_hl: Benchmark ID: {benchmark id}"

    # 3. Save results
    for node in nodes:
        if C++ build of node failed:
            result = "c++ error"
        elif any generator failed or --only [int] passed:
            result = "halide error"
        elif 0 profile batches or any Halide binary run failed:
            result = "runtime error"
        else:
            result = "success"
        node.result = best_of(node.result, result)

    # Also save benchmark set object and add to session, if criteria passed.

See the [Reference Build Commands](reference_build_commands.md) file for the
tested build/link recipe.  That file teaches the **Halide toolchain** (which
compiler/generator/link commands to run, and their gotchas) using its own
example file names.  It is deliberately NOT the source of truth for the
catalog-specific `bin/` file names — those are named as in the pseudocode above
(keyed by schedule full ID + parameters index), and `build.py` owns them.
Don't try to keep the two in sync.

**As implemented** (`build.py`): `init_build` (`cmd_init_build`) resolves
target/other/anchor (`_resolve_target`/`_resolve_other`/`_resolve_anchor`, the
target possibly a freshly created child schedule) under the session + catalog
locks, then writes `init_build.json` (catalog-relative paths) to the private
workspace.  `build` (`cmd_build`) reads that file lock-free, then
`_compile_phase` runs phase 1a (per-node `_write_ninja` → generator exe +
shared `RunGenMain.o`) and phase 1b (per-(node, params-index) `_emit` → `_link`,
with the target also publishing `bin/{i}.stmt`).  Only when profiling does it
`locks.upgrade_machine_exclusive()` **before** acquiring the catalog lock; then
`_profile_phase` runs the shuffled batches, attaching a benchmark sub-object to
each binary's source node and filling the dense benchmark-set index.
`_compute_result` derives each node's monotone result state.  A `c++ error` /
`halide error` outcome still persists the node (the result update is monotone,
never a rollback); the generator-count harness error skips the node's compile
without updating its result.


### Canon Tool (Make Canonical Tool)

    dh_hl canon -s ...

Sets the canonical schedule of the current idea node to the schedule node named by `dh_hl status`.
This is a schedule node that holds a copy of the workspace schedule.

Requirements:
    * Current idea node must exist
    * `dh_hl status` would give an unambiguous schedule node ID
    * Referenced schedule must have a `success` result state.
    * The current idea node must not already have a canonical schedule

If the command fails due to the last requirement:
    * If the schedule node is already the canonical schedule, the tool notes it was already done.
    * Otherwise, it advises the `dh_hl new_idea {canonical ID}` and `dh_hl set_idea` tools,
      where the `{canonical ID}` is the ID of the major schedule that blocked this command.

There is intentionally no "change canonical schedule" tool.


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


### New Root Tool

    dh_hl new_root -s ...

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
Or just do it now if it's naturally part of the current batch of changes.


### Set Idea Tool

    dh_hl set_idea -s ... {idea ID}

Updates the current idea state to "some current idea",
embedding the given idea node ID.
It is an error if the ID doesn't resolve to a single existing idea node.

This leaves the workspace C++ file and generator parameters alone.
To reset both the workspace files and the current idea state,
consider `restore_schedule` or `restore_idea`.


### New Idea Tool

    dh_hl new_idea -s ... {proposal name} {proposal file} [schedule ID]

IMPL TASK: pool tag, tests for two `--pool-tag`-required failure cases.

Adds a new child idea node to the referenced schedule node,
which must be a major schedule.
Furthermore, the idea node is added to the current session's
private idea list, with pool tag:

* given by `--pool-tag {pool tag}` if this argument is given

* otherwise, the pool tag of the referenced schedule node's parent.
  This fails (`--pool-tag` required) if either the referenced schedule node
  is a root node, or if its parent idea node is not in the private idea list.

It is an error if this would cause an ID collision
(i.e. the proposal name is already used).

Gives back the ID of the new idea node.

If the schedule node is a minor schedule, the tool advises:
* If its parent idea node already has a canonical schedule,
  give its ID and advise passing it explicitly to the `new_idea` tool
* If its parent idea node has no canonical schedule,
  advise `dh_hl canon` tool is appropriate if the current schedule builds
  and you are happy it correctly implements the idea.
* (no other cases: minor schedules are not root nodes by definition)


### List Ideas Tools

    dh_hl list_child_ideas -C ... [schedule ID]
    dh_hl list_seed_ideas -s ...

IMPL TASK: `list_ideas` split into two above commands

`list_child_ideas` prints a summary of each child idea node of
the referenced *major schedule* node; error if passed a minor schedule.

`list_seed_ideas` prints a summary of each seed idea of the current session.

Prints these lines for each idea node:

* The ID of the idea node (all lines except this one indented by 2)

* The proposal name

* ID of canonical schedule, or `(none)`

* For `list_child_ideas` only,
  the first up-to 72 characters of the first line of the proposal text.

* For `list_child_ideas` only,
  if the last non-empty line of the proposal text starts with
  `Created for session:`, print that line.
  (See "Session Creation Tools: Common Information").

* For each idea side link,
  print `borrowed from: {idea short ID}`
  or `superseded by: {idea short ID}` as appropriate.


### View Idea Tools

    # All commands do not acquire session lock
    dh_hl view_idea -C ... {idea ID}

IMPL TASK: no more `view_session_idea`

Prints the referenced idea node's

* proposal name

* full proposal text

* list of child schedule IDs, one line each

* idea side links, in the same format as `list_child_ideas`

IMPL TASK: gap 4 fixed


### Add Idea Side Link Tool

    # Reads like a sentence, e.g. abcdef.foo borrows_from 123456.bar
    dh_hl add_idea_side_link -C ... {idea ID lhs} {type} {idea ID rhs}

Add an idea side link from the LHS idea to the RHS idea,
of type `borrows_from` or `superseded_by`.
Silent no-op if this exactly duplicates an existing idea side link.
(i.e. same LHS, RHS, and type).


### Add Warning Toggle Tool

    dh_hl add_warning_toggle {schedule ID} {commentary ID}

Add a new `WarningToggle` sub object to the referenced schedule,
which cites the referenced commentary.

This command takes further arguments:

* `--block {rule} {func}` makes the new `WarningToggle` block
  warnings with the given rule name and function name.

* `--cancel {WarningToggle ID}` makes the `WarningToggle`
  cancel the effects of the given other object (i.e. un-block).

Exactly one of `--block` / `--cancel` must be given, since a `WarningToggle`'s
value is a tagged union (see "WarningToggle State").

IMPL TASK: gap 2, eliminate `session_root_node`

The schedule given by `dh_hl session_root_of` is a reasonable
default for the `{schedule ID}` argument.
This scopes the "lesson" that the warning is to be ignored mostly to
schedules worked on in this session, but not to those from other
sessions, which may be working on schedules completely different from
this one.

FUTURE: warning for unknown warning rule name or func name.

FUTURE: automate schedule ID, but the defaults for `[schedule ID]`
are probably not appropriate for this command.


### Debug Warning Toggle Tool

    dh_hl debug_warning_toggle [schedule ID]

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


### View Benchmark Warnings Tool

    dh_hl view_benchmark_warnings {benchmark ID}

Pretty-print the warnings embedded in the referenced benchmark sub-object.
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


### View Benchmark Stdout Tool

    dh_hl view_benchmark_stdout {benchmark ID}

Print the `stdout` captured for the named benchmark.


### History Tool

    dh_hl history -C ... [schedule ID]

Walks the branch of the tree from the referenced schedule node
up toward a root node.
For each schedule node, prints:

IMPL TASK: gap 4 fixed

* Its ID
* Its child idea nodes in the same format as `dh_hl list_child_ideas`,
  marking the child idea node that is the parent of the previously printed schedule node.
* For each commentary file, its timestamp on one line,
  and the first up-to-72 characters of the first line of the commentary text.


### List Schedules Tools

    # Does not acquire session lock
    dh_hl list_output_schedules -s ...
    dh_hl list_sibling_schedules -C ... [schedule ID]
    dh_hl list_child_schedules -C ... {idea ID}
    dh_hl list_equal_schedules -C ... [schedule ID]

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

IMPL TASK: add this

Takes an optional `--brief` argument.

For each output schedule node of the current session, prints:

* A prominent banner with the schedule node's ID.

* The outputs of `view_all_commentary` for the schedule node,
  inheriting the `--brief` behavior.

Error if the current session has no output yet.


### Root Query Tools

    # Does not acquire session lock
    dh_hl root_of -C ... [schedule ID]
    dh_hl session_root_of -s ... [schedule ID]

IMPL TASK: add this, including testing of `session_root_of` failure,
and weird case where two seed ideas have an indirect parent/child
relationship (should return the indirect child's child schedule,
if found first). Also the timestamp failure needs testing.

IMPL TASK: remember this tool satisfies the conditions for a
timestamp check to be included (infinite loops possible).

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

Adds the referenced schedule node as a child and the canonical
schedule of the referenced idea node.

This fails if:
* The referenced schedule node is not a root node.
* The referenced idea node already has a canonical schedule.
* The new edge would cause a tree structure invariant violation.

Rarely needed, mostly for when a new root node was created and you regret it.


### Session Creation Tools: Common Information

IMPL TASK: no more private workspace initialization.

IMPL TASK: inform me if there's a potential contradiction with
the private workspace not being initialized, but somehow the
session lock still has to exist.
My POV for now is the `private/...` *directory* can exist,
but the files within it won't be created.
The "actual files exist" checks are the failure criteria
for `init_workspace` without `--force`.

IMPL TASK: prompt and multiple seed ideas

IMPL TASK: per-tool default anchor node

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

The new catalog directory is named by the `-C` argument.
The requirement for `-C` is *opposite* all other commands:
it is an error if the named directory *does* exist.

The behavior is as-if a single schedule node were created,
then a new session is created with that schedule as the only parent schedule.
The new session node has no parent session and has no default anchor node.
This is because the user-provided schedule may be very poor,
so it's not a reasonable default as an anchor (profiling may never terminate)


### New Sub Session Tool

    dh_hl new_sub_session -s ... {proposal name} {prompt file} [schedule IDs...]

IMPL TASK: test the default anchor behavior

IMPL TASK: test multiple parent schedules, and empty list behavior.

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

IMPL TASK: gap 3, or lack thereof.
This uses all output schedules as parent schedules,
not giving special treatment for the primary output schedule.

The current session must be self-closed and have depth 0.

Create a new successor session (depth = 0) with

* the current session as its parent.

* the output schedules of the current session as
  the parent schedules for session creation.

* the primary output of the current session as
  its default anchor.


### Catalog Location Tool

    dh_hl catalog_location -C ...

Print the path to the catalog directory.
Non-trivial when the `-s {session handle}` option is used.


### List Sessions Tools

    dh_hl list_open_sessions -C ...
    dh_hl list_termini -C ...

List all open session nodes or all termini ("terminuses") of the current catalog.
Give both full session IDs and session handles.


### Close Session Tool

    dh_hl close_session -s ... [schedule IDs...]

IMPL TASK: basically needs a rewrite for new functionality

IMPL TASK: tests for failed pool tag (2 reasons, as with `new_idea`,
although "not in private idea list" may be very hard to construct.
If it's too hard, it's acceptable to keep `_forget_private_idea` internally)

Add outputs to the current session, making it self-closed.
This promotes a fair amount of private (not git tracked)
session state to public (git tracked) session node state.

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

If any schedule node given has no commentary,
the tool gives an error and reminds of the `dh_hl comment` tool.

**Output Benchmark Sets:**
Same as the current session's private benchmark set list.

IMPL TASK: gap 2, eliminate `session_root_node`

**Added superseded-by Links:**
For each output schedule `O`, find the schedule node `R`
that would be found by `dh_hl session_root_of O`.
Add a superseded-by idea side link from `R`'s parent to `O`'s parent.
This step is silently skipped for output schedules
where `session_root_of` would fail.


### Join Session Tool

    # Acquires session lock of the current session only.
    dh_hl join_session -s {current session handle/ID} {joined session handle/ID}

IMPL TASK: this is the only tool that has to reference two sessions.
Advise me if this causes any serious complications for existing CLI helpers
or otherwise breaks (possibly unstated) implementation assumptions.
Note, the locking discipline doesn't require locking the joined session
since this tool doesn't access any of its private workspace state.

Adds joined session outputs to the current session's private idea list
and private benchmark sets; error if the joined session lacks outputs.
Accepts `--dry-run` and `--pool-prefix {pool prefix}` arguments.
The default `pool prefix` is an empty string.

If `--dry-run` is not given,

* Add all benchmark sets from the joined session's output to the
  current session's private benchmark set list.

* For each joined session's output schedule node,
  add its parent idea node to the current idea list,
  with the assigned pool tag being:
  
  * the existing pool tag unchanged, if the idea was already in the list
  
  * the output schedule node's pool tag otherwise,
    prefixed with `{pool prefix}.` if the pool prefix is non-empty.

IMPL TASK: an unfriendly raw Python exception is acceptable for the
root schedule case since this shouldn't happen without manually
corrupting the catalog files.

Whether or not `--dry-run` was given, this prints out

* The benchmark sets (that would be) added,
  each on a line of the form `dh_hl: add benchmark set {id}`

* The idea nodes (that would be) added, as two lines of the form
  `dh_hl: add idea {id}`, `dh_hl: pool tag {pool tag}`.
  The pool tag given includes the added prefix.

IMPL TASK: add a flag to `safety.commit` that enables asserting that
no files were added (`not _new_entries`) and no overwrites queued.
Use this flag to self-check that `--dry-run` did nothing.
NB locking intentionally bypasses this so that won't mess anything up.

IMPL TASK: test prioritization of existing pool tag.

IMPL TASK: test missing joined session outputs failure.


### Delist Session Tool

    dh_hl delist_session -s ...

Set the is-delisted flag of the current session to true.
Useful to get rid of old abandoned sessions in the open sessions or termini list.


### View Session Prompt Tool

    # Does not acquire session lock
    dh_hl view_session_prompt -s ...

IMPL TASK: add this

Prints the plain text prompt of the current session,
followed by `=== Seed Ideas ===`,
followed by the output of the `list_seed_ideas` tool.


### List Session Private Ideas Tool

    dh_hl list_private_ideas -s ... [N]
    dh_hl list_private_ideas_todo -s ... [N]
    dh_hl list_private_ideas_done -s ... [N]

IMPL TASK: pytest skip `list_private_ideas*` and ignore for now
that the tool won't work well with new unordered current ideas state.
Printing in cost-ordered way will be left to a future batch of tasks.
For now, session private idea list can be tested with
`get_pool_tag` including the error case.  Please write tests that have
multiple session nodes as a guard against future accidental mixing of
different sessions' states.

FUTURE: add tool, nonfunctional for now.


### Session Current Anchor Tools

    dh_hl get_current_anchor -s ...
    dh_hl set_current_anchor -s ... [schedule ID]

IMPL TASK: add this

Get or set the ID of the current session's current anchor node.
The special value `none` is used for "no anchor node"
(both for get and set commands).


### Session Idea Node Pool Tag Tools

    dh_hl get_pool_tag -s ... {idea ID}
    dh_hl set_pool_tag -s ... {idea ID} {pool tag}
    dh_hl hide_private_idea -s ... {idea ID}

IMPL TASK: add this

IMPL TASK: remove `forget_private_idea`

Gets or sets the pool tag assigned for the given idea node,
as stored in the private idea list.
`set_pool_tag` implicitly adds to the list if necessary.
`hide_private_idea` simply prepends a `.` to the idea node's pool tag.

`get_pool_tag` and `hide_private_idea` error out if the idea node
is not in the private idea list.

The purpose of the pool tag is to allow the agent to enforce some
diversity in the frontier of "best ideas" shown in `list_private_ideas`.
Ideas are ranked only within their pools.

FUTURE: actually implement the last paragraph in a later turn.
Also the `.` prefix won't make sense until then.


### Rename Pool Tag Tool

    dh_hl rename_pool_tag -s ... {pool tag before} {pool tag after}

IMPL TASK: add this, test results with `get_pool_tag`

Iterates over all entries in the current session's private idea list.
Each idea that has `{pool tag before}` as its pool tag
gets its pool tag updated to `{pool tag after}`.

Prints `{count} idea nodes updated`.


### Copy Schedule, ID-of Schedule Tools

    # All commands do not acquire the session lock
    dh_hl copy_schedule -C ... {output file} [schedule ID]
    dh_hl copy_terminus_schedule -C ... {output file}
    dh_hl copy_seed_schedule -s ... {output file}
    dh_hl copy_session_output -s ... {output file}

    dh_hl terminus_schedule_short_id -C ...
    dh_hl seed_schedule_short_id -s ...
    dh_hl session_output_short_id -s ...

    dh_hl terminus_schedule_full_id -C ...
    dh_hl seed_schedule_full_id -s ...
    dh_hl session_output_full_id -s ...

Find a certain schedule node (noun), and do something with it (verb):

**Nouns:**

* `schedule`: the schedule node id'd by `[schedule ID]`.

IMPL TASK: gap 3, explicit primary output schedule

* `terminus_schedule`:
  the primary output schedule of the unique terminus.
  Error if there is not exactly one session node that is a terminus
  or the terminus has no output schedule.

IMPL TASK: gap 3, explicit 0th seed idea

* `seed_schedule`:
  the canonical schedule of the current session's 0th seed idea.

IMPL TASK: gap 3, explicit primary output schedule

* `session_output`:
  the primary output schedule of the current session;
  error if there is no output has been defined yet.

**Verbs:**

* `copy`: write the C++ schedule to the given `{output file}`.

* `full_id`: give the full ID of the schedule node

* `short_id`: give a short ID of the schedule node (may fall back to full ID)

NB see also `schedule_full_id`, `schedule_short_id`, `restore_schedule` tools.


### View Generator Parameters Tool

    dh_hl view_generator_parameters [schedule ID]

Pretty-print the `generator_parameters.json` stored in the named schedule node.
Each generator parameters object is printed as a single line

    [0-based index] [JSON object as one line]


### Init Workspace Tools

    dh_hl init_workspace -s ...

Takes an optional `--force` flag.

Initialize the session private workspace state to defaults:

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

Unless `--force` is given, the tool fails if any existing state would
be overwritten.  (Implemented by writing each file via
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


### ID Translation Tools

    # All commands do not acquire session lock
    dh_hl schedule_full_id -C ... [schedule ID]  # Print the full ID of the given schedule node
    dh_hl schedule_short_id -C ... [schedule ID] # Print a short ID for the given schedule node
    dh_hl idea_full_id -C ... {idea ID}          # Print the full ID of the given idea node
    dh_hl idea_short_id -C ... {idea ID}         # Print a short ID for the given idea node
    dh_hl session_full_id -s ...                 # Print the full ID of the current session
    dh_hl session_handle -s ...                  # Print the session handle for the current session

On success: print out the ID/handle with a newline, and no other `stdout` output.

Short ID getters may silently fall back to giving a full ID.
However, the `session_handle` getter will never give back a session full ID:
this is load bearing for correctness, since it encodes more than a session full ID
(namely, the catalog directory location).


### JSON Schedule Info Tool

    dh_hl json_schedule_info -C ... [schedule ID]

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


### JSON Idea Info Tool

    dh_hl json_idea_info -C ... {idea ID}

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


### JSON Session Info Tool

    # Does not acquire session lock
    dh_hl json_session_info -s ...

IMPL TASK: `prompt`, `default_anchor_schedule`, `seed_ideas`, `output_*`

Prints the state of the current session as a JSON object, with key/value pairs

* `id`: full ID of node

* `parent`: string or null, full ID of parent session

* `children`: list of strings, each a full ID of a session node

* `prompt`: string

* `default_anchor_schedule`: string or null,
  full ID of default anchor schedule node

* `seed_ideas`: list of strings, full ID of seed idea nodes

* `output_schedules`: list of strings or null,
  full ID of output schedule nodes

* `output_benchmark_sets`: list of strings or null,
  full IDs of output benchmark sets

* `delisted`: bool

* `depth`: number


### JSON Benchmark Info Tool

    dh_hl json_benchmark_info -C ... {benchmark ID}

Prints the identified benchmark in Benchmark JSON format


### JSON Benchmark Set Info Tool

    dh_hl json_benchmark_set_info -C ... {benchmark set ID}

Prints the state of the referenced benchmark set as a JSON object.


### JSON Export Tool

    dh_hl json_export -C ...

Exports the entire catalog as a JSON object, with key/value pairs

* `ideas`: idea nodes

* `schedules`: schedule nodes

* `sessions`: session nodes

* `benchmark_sets`: benchmark set objects

Each value is itself an object, with keys being string full ID and values
being JSON objects in the same format as `json_schedule_info`,
`json_idea_info`, `json_session_info`, `json_benchmark_set_info`.

FUTURE: holds the exclusive catalog lock despite being conceptually read-only.
Optimize this if needed, but this shouldn't be in the agent hot loop.


### Fix Canonical Tool

    dh_hl fix_canonical -C ... {idea ID}

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


## Main Agent Default Session Behavior

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
