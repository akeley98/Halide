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
  TODO: this is the part that doesn't exist yet.
  Each agent session is tracked historically as a "session node".
  The catalog data structure on-disk is robust to multiple concurrent agent sessions,
  and implements a machine-wide lockout that prevents benchmarking from
  competing with other harness usage for CPU time.

* Implemented as a **Python 3 package** for now, launched with the `dh_hl` stub.

AGENTS: if you are **implementing** this harness,
see the companion [Implementation Notes](impl.md).


## Conceptual State

The catalog is stored in a directory whose name ends with `.dh_hl`.

The catalog primarily consists of a bipartite tree consisting of:

* **Schedule Nodes:** Holds C++ generator file and associated benchmarking information and commentary.
  May have 0 or 1 "idea nodes" as parents.
  May have 0 or more idea nodes as children as well only if this is a major schedule (to be defined).
  The schedule is embedded with a UTC wall time timestamp.

* **Idea Nodes:** Holds a reference to exactly 1 parent schedule node,
  and includes a text proposal of how to further modify the schedule.
  The child schedule nodes are attempts of implementing the idea.
  Up to one of the child schedules is the idea node's **canonical schedule**.

Furthermore, there is a side tree of **Session Nodes**
representing the progress and workspace of a single agent.
This tree contains pointers to the primary schedule/idea tree.
Multiple agents can work on the same catalog in parallel,
but each must have its own session.

A schedule node is a **root node** if it has 0 parents.

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

* **C++ source code**

* **UTC wall time timestamp:** timestamp of when the schedule node was created.

* **Schedule Node Full ID:** `{timestamp}_{hash}` where `hash` is that of the stored C++ code encoded as UTF-8.
  Exactly 90 characters.

* **Edges:** 0 or 1 parent idea nodes, 0 or more child idea nodes.

* **Result:** C++ compiler error, Halide compiler error, or success.

* **Benchmark Result Files** JSON format, documented later

* **Commentary Files:** Contains timestamp, optional integer importance value, commentary text.
  For minor schedules, should be used to explain what went wrong with this implementation attempt.
  For major schedules, should be used as a post-mortem, or commentary on the effectiveness of the change
  implemented from the idea.
  The harness doesn't enforce these "shoulds"

FUTURE: consider how to store history of sweeping over `GeneratorParam` values.
For now, the required default value of the `GeneratorParam`
may be used as an out-of-band method to recommend the "official" parameter values.


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

* **Importance:** derived state; higher is more important.
  If there exists no canonical schedule: negative infinity
  If there exists a canonical schedule with no commentary containing importance values: 0
  Otherwise: maximum of all commentary importance values.
  Note: this design means adding commentary with negative importance can "demote" a 0-importance node.


# (Temporary) The Goals of the new Session/Concurrency Rewrite

* Harness now needs to be able to handle concurrent usage and locking.
  I need locking both to prevent simultaneous catalog edits and to force monopolizing the machine
  when running profiling.

* New session nodes, used to represent a single main agent or sub-agent session.
  It's opened with a seed idea node, gets closed with an output schedule node,
  and contains a private workspace containing an untracked C++ schedule and current idea state.

* The session nodes form a graph separate from the main idea/schedule graph,
  tracking history of sessions and main/sub-agent relations.
  A production usage may not care about this history, but it'll be useful for research.

* The catalog alone is now the only artifact that is intended to be checked into git.
  Get rid of the concurrency-killing single workspace file, `bin/`, and current idea state.
  Since there's no C++ file in plain sight anymore,
  the "final schedule" will have to be deduced by querying the results of the recent session.

* Each agent needs to have a "current session", which controls which session node they're associated with.
  Most `dh_hl` commands will require a current session argument (`-s` `--session`)
  and a current catalog directory argument (`-C` `--catalog`).
  The latter takes the place of deducing the catalog directory from the (eliminated) single workspace file.
  This is made easier with session handles.


### Session Node State

* **Seed Idea:** Mandatory reference to an idea node.
  For sub-agents, the proposal of the idea is meant to be the prompt.

* **Output Schedule:** Optional reference to a schedule node.
  This is the "final result" of the session,
  and the commentary should be used to summarize the session findings.

* **Is-delisted Flag:** Initially false.

* **Depth** (int); top-level sessions have 0 depth.

* **Parent Session:** Optional, reference to another session node.

* **Session Node Full ID:** `{depth}_{timestamp}_{username}@{hostname}`.
  This is, for now, intentionally de-anonymizing.
  The `username` and `hostname` are sanitized.

* **Session Private Workspace** state: gitignore'd per-session-node state.
  This contains a session lock, current idea state, a workspace C++ schedule, and a `bin` directory.

Most harness tools require a "current session",
which is identified with the catalog directory
and the full ID of a session node within the catalog.
The pair can be succinctly communicated using "session handles",
described a few sections later.

**Session Golden Rule:** two concurrent agents must never have the current session.
The session lock (see "Locking") will catch many such violations,
but will not prevent observing a partial edit to the workspace C++ file.


### Current Idea State

We need to keep track of which idea the schedule in the workspace should be parented to.
The "current idea state" stored in the current session is a tagged union of

* **No current idea state**: contains a timestamp;
  indicates the workspace schedule is to become a root node.

* **Some current idea state**: contains full ID of an idea node.


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

The catalog is a tree of schedules,
so it's not necessarily clear which one is the "final" schedule.

The convention is this: there usually should be only one terminus,
it should be closed,
and its output schedule is the "final result" of LLM-guided scheduling so far.

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

Each short ID matches some number of nodes.
The short ID resolves successfully iff it matches exactly 1 node.
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

**Warning:** short IDs may become invalid due to new ambiguities.
Use them only as convenient IDs for immediate tool use,
and not long-term identification (e.g. in commentary).


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
The profiling step of `profile` and any `exec_exclusive` command
acquire the machine lock exclusively.

All tools that access a catalog acquire an exclusive per-catalog **catalog lock**.

All tools that require a current session (`-s`) acquire an exclusive per-session **session lock**,
except for a subset of read-only commands, marked when their syntax is introduced.
This locking *never* fails for correct usage: failure to acquire is diagnosed as an error
(two concurrent agents using the same session).

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


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


### Help Tool

    dh_hl help [command]

With no `[command]`, lists all commands briefly; with a `[command]`, describes that one.


### Status Tool

    # Does not acquire session lock
    dh_hl status -s ...

This is a purely read-only command.
Agents MUST run this on startup, before first editing the workspace C++ file.

If there was no current session given, the tool errors.
Otherwise, the tool tries to find a schedule node that already holds the workspace C++ file
and give basic information on the current catalog state.

**Outputs:**

* The full IDs of the current session and its parent session (if any)

* The is-delisted flag of the current session

* The IDs of the session's seed idea node and output schedule node
  (may be none for the latter)

* The current idea state,
  whether the current idea node exists,
  and the canonical schedule for it, if any.

* The ID of the **unambiguous schedule node**, if it exists.
  This is the schedule node that holds a copy of the workspace C++ file (matched by hash)
  and has a parenting status matching the current idea state:
    - **no current idea:** is a root node whose timestamp matches the current idea state
    - **some current idea:** its parent is the current idea node.

* The status as one of
    - "no workspace C++ file"
    - "workspace inconsistent, unknown schedule"
      (could not find any stored schedule matching the current workspace C++ file)
    - "workspace inconsistent, unexpected current idea state"
      (found stored schedule in catalog, but none were unambiguous)
    - "workspace consistent"
      (unambiguous schedule node found)

* On either inconsistent status, also prints a warning that the workspace
  may have been edited outside the harness (see Rationale below).

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->

**Rationale:**

A workspace C++ file is in "consistent state"
when it unambiguously corresponds to a schedule node whose
parent idea is what we expected.
Essentially, this was "where we left off" when we last stopped searching.
As soon as we start editing the file, it'll be in inconsistent state.

We need the current idea state to remember what idea we were working on,
since we have no idea otherwise as soon as the schedule hash changes.

Regarding the warning, as soon as the workspace C++ file is edited,
it'll be in inconsistent state, which is fine if done on purpose.
(it'll soon be added to the catalog once the agent starts the build).
But if this was the case before the agent started editing at all,
something is wrong: the file may have been edited in an undisciplined
way outside the harness, and we should not blindly proceed and potentially
parent the schedule to an idea that has nothing to do with what is actually being explored.

This is particularly a risk of storing the private session state
outside the git-tracked state. It's not impossible some heavy-handed
git merging could cause the session private workspace to desync.


### Restore Tool

    dh_hl restore -s ... {schedule ID}

Copies the schedule node's C++ schedule to the workspace C++ file,
and updates the current idea state as follows,
depending on the number of parent idea nodes of the referenced schedule node.

* **No parents:** set to "no current idea" state, embedding the timestamp of the schedule node.

* **One parent:** set to "some current idea" state, embedding the ID of the parent idea node.


### Build Tool

    dh_hl build -s ... [parameters file]

This tool compiles the workspace file and adds/updates a schedule node for it. It:

1. Compiles the Halide binary in the session private workspace `bin` directory.
2. Finds or creates the edited schedule node.
3. Conditionally updates the result state of the edited schedule node.
4. Prints the ID of the edited schedule node.

The edited schedule node is:

* If `dh_hl status` would give an unambiguous schedule node,
  that schedule node is the one this tool edits.
* Otherwise, if there is no current idea node,
  the tool errors, suggesting the `set_idea` and `new_root` tools.
* Otherwise, the tool adds a new child schedule node to the current idea node,
  holding a copy of the workspace file.

The build, along with a plain `.stmt` and a `conceptual.stmt` file
(the lowered and pre-lowering loop nests, respectively),
is in the gitignore'd `bin` directory of the current session.
Depending on the build outcome, the result state of the edited schedule node is updated to one of:

* `c++ error`: couldn't even compile the C++ workspace file (worst)
* `halide error`: passed said step, but Halide generator exited unsuccessfully
* `success`: both steps exited successfully (best)

However, the update is to the better of the previous and new value.
This is to account for how some generator parameter values may cause the
Halide generator to fail; doesn't mean the entire schedule is bad.

The parameters file is in Generator Parameters JSON Object Format
(documented later); there are no parameters if the file is omitted.
These parameters are passed through to the Halide generator.

This tool exits successfully iff no harness errors occurred
and all subprocesses succeeded.

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


### Profile Tool

    dh_hl profile -s ... [parameters file]

This is like `dh_hl build` except
* The Halide binary is run with Andrew Adams's new profiler tool
  and the benchmark results are recorded.
* The parameters file may contain a list of generator parameters JSON object,
  with each parameter set profiled in turn.

The list of generator parameters JSON objects for the command is
* `[{}]`, if the parameters file was not given
* `[obj]`, if the parameters file encodes a single JSON object `obj`
* The parsed contents of the parameters file, verbatim, if it's already a list

Step 1 of the `dh_hl build` command is modified to only create a Halide generator
and omit the `stmt` file generation.

Step 3 of the `dh_hl build` command is modified to become a
loop over this list, with the machine lock held exclusively.
The Halide binary is generated and benchmarked
once using each generator parameters object, with a benchmark object
saved and the schedule node result state updated each time.

Doesn't fail irrecoverably if some builds fail; the tool skips them and moves on.

This tool exits successfully iff no harness errors occurred
and all subprocesses succeeded.

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


### Canon Tool

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

Adds a new commentary file to the referenced schedule node,
with contents copied from the passed `commentary file`.
The commentary has no importance value.


### Comment With Importance Tool

    dh_hl comment_importance -C ... {commentary file} {importance} [schedule ID]

Like the `comment` tool but with the addition of the importance value.


### New Root Tool

    dh_hl new_root -s ...

Hashes the file and looks for existing schedule nodes with the same hash.
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

and with no importance value attached.
This is just a temporary "bare minimum" merge conflict resolution.

FUTURE: probably remove this extra merge conflict recovery functionality later.
Or just do it now if it's naturally part of the current batch of changes.


### Set Idea Tool

    dh_hl set_idea -s ... {idea ID}

Updates the current idea state to "some current idea",
embedding the given idea node ID.
It is an error if the ID doesn't resolve to a single existing idea node.


### New Idea Tool

    dh_hl new_idea -s ... {proposal name} {proposal file} [schedule ID]

Adds a new child idea node to the referenced schedule node,
which must be a major schedule.

It is an error if this would cause an ID collision (i.e. the proposal name is already used).

Gives back the ID of the new idea node.

If the schedule node is a minor schedule, the tool advises:
* If its parent idea node already has a canonical schedule,
  give its ID and advise passing it explicitly to the `new_idea` tool
* If its parent idea node has no canonical schedule,
  advise `dh_hl canon` tool is appropriate if the current schedule builds
  and you are happy it correctly implements the idea.
* (no other cases: minor schedules are not root nodes by definition)


### List Ideas Tool

    dh_hl list_ideas -C ... [schedule ID]

It is an error if the referenced schedule node is not a major schedule.

For each child idea node of the referenced schedule node, prints three lines:

* The ID of the idea node
* The proposal name (indent by 2)
* The first up-to 72 characters of the first line of the proposal text (indent by 2)


### View Idea Tools

    # All commands do not acquire session lock
    dh_hl view_idea -C ... {idea ID}
    dh_hl view_session_idea -s ...

Prints the referenced idea node's

* proposal name
* full proposal text
* list of child schedule IDs, one line each

`view_session_idea` references the current session's seed idea.


### History Tool

    dh_hl history -C ... [schedule ID]

Walks the branch of the tree from the referenced schedule node
up toward a root node.
For each schedule node, prints:

* Its ID
* Its child idea nodes in the same format as `dh_hl list_ideas`,
  marking the child idea node that is the parent of the previously printed schedule node.
* For each commentary file, its timestamp on one line,
  and the first up-to-72 characters of the first line of the commentary text.

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


### List Schedules Tools

    dh_hl list_sibling_schedules -C ... [schedule ID]
    dh_hl list_child_schedules -C ... {idea ID}
    dh_hl list_equal_schedules -C ... [schedule ID]

Lists all schedule nodes matching some criterion:

* `list_sibling_schedules`: list all schedule nodes that have the same parent as the given schedule.
  Error if a root node is given.

* `list_child_schedules`: list all children of the given idea node.
  (This partially overlaps the `view_idea` tool, with different verbosity).

* `list_equal_schedules`: list all schedule nodes with the same hash as the given schedule.

Each schedule is printed in the same manner as `dh_hl history`
(ignoring the "marking the child idea node" part),
with a clear separator between each.
There is no predefined order of the schedules.


### View Commentary Tool

    dh_hl view_commentary -C ... [schedule ID]

Print all commentary of the referenced schedule node.

Prints each commentary file separated by dividers, with its

* timestamp
* importance
* full text


### View Session Commentary Tool

    # Does not acquire session lock
    dh_hl view_session_commentary -s ...

Similar to `view_commentary`, except

* The referenced schedule node is the output schedule node of the current session (error if not yet set).
* Only commentary with a positive importance value is printed.


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

The following session-creation tools create session nodes and idea nodes in pairs.
The process starts with a given parent schedule node:

* A new session ID is allocated.

* A new idea node is created from the proposal name and proposal file,
  in the same manner as `dh_hl new_idea {proposal name} {proposal file} {parent schedule ID}`,
  except the proposal text has this line appended:

    Created for session: {session_id}

* Create a new session seeded with the new idea node.
  The session private workspace is initialized with the parent schedule node's C++ file,
  and with "some current idea" state pointing to the new idea node.

* A new schedule node is created, holding a copy of the parent schedule node's C++ file.
  This is immediately set as the canonical schedule of the new idea node.

* Allocate a session handle for the new session, and print it for the user.

The duplicate schedule node is somewhat hacky,
but ensures that a new session can immediately assume it's given
an exclusive sub-tree to explore.
It's expected the prompt will be fairly "high level",
and not comparable to most idea nodes in complexity.
So, the agent can start generating more short-term ideas
for a parent schedule that's exclusively its own.


### New Catalog Tool

    dh_hl new_catalog -C ... {proposal name} {proposal file} {input C++ file}

Creates a new catalog directory with the bare minimum state to get started:

* Two schedule nodes, both holding a copy of the input C++ file.

* One idea node connecting the two schedule nodes.

* One top-level session node (terminus) seeded with that idea node.

The new catalog directory is named by the `-C` argument.
The requirement for `-C` is *opposite* all other commands:
it is an error if the named directory *does* exist.

The behavior is as-if a single schedule node were created,
and then a new session/idea pair created with that schedule as the parent schedule.
The new session node has no parent session.


### New Sub Session Tool

    dh_hl new_sub_session -s ... {proposal name} {proposal file} [schedule ID]

Create a new session/idea pair, with the given parent schedule.
The new session is a child of the current session with 1 greater depth.


### New Successor Session Tool

    dh_hl new_successor_session -s ... {proposal name} {proposal file}

The current session must be self-closed and have depth 0.
Create a new session/idea pair, with the output schedule of the current session as the parent schedule.


### List Sessions Tools

    dh_hl list_open_sessions -C ...
    dh_hl list_termini -C ...

List all open session nodes or all termini ("terminuses") of the current catalog.
Give both full session IDs and session handles.


### Close Session Tool

    dh_hl close_session -s ... [schedule ID]

Set the given schedule node to be the current session's output schedule node.
Error if the current session already has an output schedule node,
or if the given schedule node has no commentary with positive importance.
In the latter case, remind the caller of the `comment_importance` tool.


### Delist Session Tool

    dh_hl delist_session -s ...

Set the is-delisted flag of the current session to true.
Useful to get rid of old abandoned sessions in the open sessions or termini list.


### Copy Schedule, ID-of Schedule Tools

    # All commands do not acquire the session lock
    dh_hl copy_schedule -C ... {output file} [schedule ID]
    dh_hl copy_terminus_schedule -C ... {output file}
    dh_hl copy_session_seed_schedule -s ... {output file}
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

* `terminus_schedule`: the output schedule of the unique terminus.
  Error if there is not exactly one session node that is a terminus
  or the terminus has no output schedule.

* `session_seed_schedule`: the canonical schedule of the current session's seed idea.

* `session_output`: the output schedule of the current session;
  error if there is no output schedule yet.

**Verbs:**

* `copy`: write the C++ schedule to the given `{output file}`.

* `full_id`: give the full ID of the schedule node

* `short_id`: give a short ID of the schedule node (may fall back to full ID)

NB see also `schedule_full_id`, `schedule_short_id`, `restore` tools.


### Workspace Location Tools

    dh_hl workspace_schedule -s ...
    dh_hl workspace_bin -s ...

Get the filename of the workspace C++ file or bin directory, respectively.


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

* `timestamp`: string, timestamp

* `hash`: string

* `result`: string, `result.txt` result

* `benchmark`: list of objects,
  each benchmark file becomes one benchmark JSON object (described later)

* `commentary`: list of objects, one for each commentary file.
  Has key/value pairs `timestamp` (formatted string timestamp value),
  `importance` (number if importance exists, null if not),
  `text` (string contents).


### JSON Idea Info Tool

    dh_hl json_idea_info -C ... {idea ID}

Prints the state of the referenced idea node as a JSON object, with key/value pairs

* `id`: full ID of node

* `parent`: string, full ID of parent schedule

* `children`: list of strings, each a full ID of a child node

* `proposal_name`: string

* `proposal_text`: string

* `canonical_schedule`: null if no canonical schedule, otherwise string full ID of the canonical schedule

* `importance`: number if finite, null for negative infinity


### JSON Session Info Tool

    # Does not acquire session lock
    dh_hl json_session_info -s ...

Prints the state of the current session as a JSON object, with key/value pairs

* `id`: full ID of node

* `parent`: string or null, full ID of parent session

* `children`: list of strings, each a full ID of a session node

* `seed_idea`: string, full ID of seed idea node

* `output_schedule`: string or null, full ID of output schedule node

* `delisted`: bool

* `depth`: number


### JSON Export Tool

    dh_hl json_export -C ...

Exports the entire catalog as a JSON object, with key/value pairs

* `ideas`: idea nodes

* `schedules`: schedule nodes

* `sessions`: session nodes

Each value is itself an object, with keys being string full ID and values
being JSON objects in the same format as the above JSON tools.

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


## Generator Parameters JSON Object Format

Object mapping generator parameter names to values.
Each value can be bool, number, or string.
All pairs go to the Halide generator as `key=value`.


## Benchmark JSON Format

Key value pairs:

* `hostname`: string, hostname of system used for profiling
* `cpu_count`: number, CPU count of system used for profiling
* `parameters`: object, generator parameters used to generate the profiled Halide binary
* `profiler`: the profiler JSON output should be a JSON object whose "pipelines"
  value is a list of 1 object. This is that inner object.
  (There will be more than 1 if we support multiple generators; just error for != 1 for now).

Note this is not the profiler you'll find documented on the internet.
The profiler was rewritten internally for this project.

FUTURE: once profile tool accepts explicit input sizes etc.
we need to embed that in here.
