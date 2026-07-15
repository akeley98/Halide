# Dendritic Halide Harness — Harness for agentic Halide scheduling

The process of scheduling Halide code — whether by hand or automated — is often a sort of tree search.
Schedules evolve over time into other schedules, and some plans don't work out and get back-tracked.
What I want is a system that stores a "catalog" of schedules, organized into a historic tree structure.

My goals for the Dendritic Halide Harness (`dh_hl`) are:

* **Long Term Memory Device:** Give agents a system for long-term
  coordination and progress tracking.
  Include structured commentary and suggested ideas for each schedule.
  Also, form the basis for building a future UI for humans (or planner
  agents) to oversee the scheduling process.

* **Benchmarking Hygiene:** Automatically ensure each benchmark/profiling
  result gets attached IMMEDIATELY to the C++ source code used, also
  annotated with system information.
  Corollary: all C++ source code ever compiled will be catalogued,
  allowing us to monitor stats on how often agents generate illegal schedules.
  Make life easier for now by assuming one C++ file containing a typical
  Halide::Generator setup (2-phase build, C++ -> C++ bin -> Halide bin).
  **Simplifying assumption:** the workspace C++ file registers **exactly one**
  generator. This lets the build/profile tools discover the generator name
  automatically (see the Build Tool) instead of parsing it out of the source.

* **Maximize Compatibility with Source Control:**
  "transparent"-ish on-disk state for catalog, designed to minimize merge conflicts.
  This is also why I say "catalog" and not "repository".

* Implemented as a **Python 3 package** for now even though I liketh it not.
  It is launched by the `./dh_hl` stub.
  **Dependency scope:** Python 3 standard library ONLY (argparse, hashlib,
  json, subprocess, atexit, signal, os, ...). No third-party pip packages,
  so whoever inherits this prototype needs no install step. The vendored
  `ninja_syntax.py` counts as stdlib-equivalent for our purposes.

* In the end, this will only be a sketchy prototype,
  as there is only 2 months left in my internship.

AGENTS: if you are **implementing** this harness,
see the companion [Implementation Notes](impl.md).


## Conceptual State

Each C++ file in the **workspace** (files outside the catalog) that has
ever been passed to `dh_hl` gets an associated catalog stored in a directory next to it.
The catalog is a bipartite tree consisting of:

* **Schedule Nodes:** Holds C++ generator file and associated benchmarking information and commentary.
  May have 0 or 1 "idea nodes" as parents.
  May have 0 or more idea nodes as children as well only if this is a major schedule (to be defined).
  The schedule is embedded with a UTC wall time timestamp.

* **Idea Nodes:** Holds a reference to exactly 1 parent schedule node,
  and includes a text proposal of how to further modify the schedule.
  The child schedule nodes are attempts of implementing the idea.
  Up to one of the child schedules is the idea node's **canonical schedule**.

Furthermore, there's a special **current idea state**
indicating what idea node that new schedule nodes will be parented to
(or none at all).

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

Unless otherwise specified, tools only avoid creating new violations,
and do not diagnose existing violations.

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

* **Importance:** derived state; higher is more important.
  If there exists no canonical schedule: negative infinity
  If there exists a canonical schedule with no commentary containing importance values: 0
  Otherwise: maximum of all commentary importance values.
  Note: this design means adding commentary with negative importance can "demote" a 0-importance node.


### Current Idea State

We need to keep track of which idea the schedule in the workspace should be parented to.
The "current idea state" stored in the catalog is a tagged union of

* **No current idea state**: contains a timestamp;
  indicates the workspace schedule is to become a root node.

* **Some current idea state**: contains full ID of an idea node.

FUTURE: think cautiously about whether this belongs in repo state or not.


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


## Tools

In all tools, the schedule and idea IDs implicitly refer to nodes
in the catalog associated with the workspace file.
`-` means stdin, except for the workspace file.

If the workspace file doesn't exist, the tool reports an error, except for the restore tool.

If the workspace file exists but the catalog directory doesn't,
then an error is reported for read-only commands;
otherwise, the catalog directory is implicitly created.
This catalog directory is named `{workspace file name}.dh_hl`;
see [implementation notes](impl.md) for details.

`{...}` (curly brackets) means a mandatory argument.

`[...]` (square brackets) means an optional argument.

`[schedule ID]`, if not given explicitly,
implies the unambiguous schedule node ID that would be given by `dh_hl status`,
or an error if no unambiguous schedule node ID would be given.
It is rarely needed to pass this.

The "current idea node" is nothing, if the current idea state encodes "no current idea",
and otherwise the idea node referenced by the "some current idea" state.
Commands that explicitly edit the current idea state must not error out
due to errors in the existing `current_idea_state.txt`.


### Help Tool

    dh_hl help [command]

With no `[command]`, lists all commands briefly; with a `[command]`, describes that one.


### Status Tool

    dh_hl status {workspace file name}

This is a purely read-only command.
Agents MUST run this on a workspace file first,
before the first edit to the file.

If there's no catalog directory,
the tool advises `dh_hl new_root {workspace file name}` and exits.
Otherwise, the tool tries to find a schedule node that already holds the workspace file
and give basic information on the current catalog state.

**Outputs:**

* Gives the current idea state,
  whether the current idea node exists,
  and the canonical schedule for it, if any.

* Gives the ID of the **unambiguous schedule node**, if it exists.
  This is the schedule node that holds a copy of the workspace file (matched by hash)
  and has a parenting status matching the current idea state:
    - **no current idea:** is a root node whose timestamp matches the current idea state
    - **some current idea:** its parent is the current idea node.

* Gives the status as one of
    - "workspace inconsistent, unknown schedule"
      (could not find any stored schedule matching the current workspace file)
    - "workspace inconsistent, unexpected current idea state"
      (found stored schedule in catalog, but none were unambiguous)
    - "workspace consistent"
      (unambiguous schedule node found)

* On either inconsistent status, also prints a warning that the workspace
  may have been edited outside the harness (see Rationale below).


NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


**Rationale:**

A workspace file is in "consistent state"
when it unambiguously corresponds to a schedule node whose
parent idea is what we expected.
Essentially, this was "where we left off" when we last stopped searching.
As soon as we start editing the file, it'll be in inconsistent state.

We need the current idea state to remember what idea we were working on,
since we have no idea otherwise as soon as the schedule hash changes.

Regarding the warning, as soon as the workspace file is edited,
it'll be in inconsistent state, which is fine if done on purpose.
(it'll soon be added to the catalog once the agent starts the build).
But if this was the case before the agent started editing at all,
something is wrong: the file may have been edited in an undisciplined
way outside the harness, and we should not blindly proceed and potentially
parent the schedule to an idea that has nothing to do with what is actually being explored.


### Restore Tool

    dh_hl restore {workspace file name} {schedule ID}

Copies the schedule node's C++ schedule to the workspace file,
and updates the current idea state as follows,
depending on the number of parent idea nodes of the referenced schedule node.

* **No parents:** set to "no current idea" state, embedding the timestamp of the schedule node.

* **One parent:** set to "some current idea" state, embedding the ID of the parent idea node.


### Build Tool

    dh_hl build {workspace file name} [parameters file]

This tool compiles the workspace file and adds/updates a schedule node for it. It:

1. Finds or creates the edited schedule node.
2. Compiles the Halide binary.
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
is in the gitignore'd `bin` directory of the catalog directory.
Depending on the build outcome, the result state of the edited schedule node is updated to one of:

* `c++ error`: couldn't even compile the C++ workspace file (worst)
* `halide error`: passed said step, but Halide generator exited unsuccessfully
* `success`: both steps exited successfully (best)

However, the update is to the better of the previous and new value.
This is to account for how some generator parameter values may cause the
Halide generator to fail; doesn't mean the entire schedule is bad.

The parameters file is in Generator Parameters JSON Object Format
(documented later); there are no parameters if the file is omitted.

This tool exits successfully iff no harness errors occurred
and all subprocesses succeeded.

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


### Profile Tool

    dh_hl profile {workspace file name} [parameters file]

This is like `dh_hl build` except
* The Halide binary is run with Andrew Adams's new profiler tool
  and the benchmark results are recorded.
* The parameters file may contain a list of generator parameters JSON object,
  with each parameter set profiled in turn.

The list of generator parameters JSON objects for the command is
* `[{}]`, if the parameters file was not given
* `[obj]`, if the parameters file encodes a single JSON object `obj`
* The parsed contents of the parameters file, verbatim, if it's already a list

Steps 2 and 3 of the `dh_hl build` command are modified to become a
loop over this list. The Halide binary is generated and benchmarked
once using each generator parameters object, with a benchmark object
saved and the schedule node result state updated each time.

Doesn't fail irrecoverably if some builds fail; the tool skips them and moves on.

NOTE: [link to implementation details](impl.md) <!-- Update both docs if you change the tool! -->


### Canon Tool

    dh_hl canon {workspace file name}

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

    dh_hl comment {workspace file name} {commentary file} [schedule ID]

Adds a new commentary file to the referenced schedule node,
with contents copied from the passed `commentary file`.
The commentary has no importance value.


### Comment With Importance Tool

    dh_hl comment_importance {workspace file name} {commentary file} {importance} [schedule ID]

Like the `comment` tool but with the addition of the importance value.


### New Root Tool

    dh_hl new_root {workspace file name}

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


### Set Idea Tool

    dh_hl set_idea {workspace file name} {idea ID}

Updates the current idea state to "some current idea",
embedding the given idea node ID.
It is an error if the ID doesn't resolve to a single existing idea node.


### New Idea Tool

    dh_hl new_idea {workspace file name} {proposal name} {proposal file} [schedule ID]

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

    dh_hl list_ideas {workspace file name} [schedule ID]

It is an error if the referenced schedule node is not a major schedule.

For each child idea node of the referenced schedule node, prints three lines:

* The ID of the idea node
* The proposal name (indent by 2)
* The first up-to 72 characters of the first line of the proposal text (indent by 2)


### View Idea Tool

    dh_hl view_idea {workspace file name} {idea ID}

Prints the referenced idea node's

* proposal name
* full proposal text
* list of child schedule IDs, one line each


### History Tool

    dh_hl history {workspace file name} [schedule ID]

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

    dh_hl list_sibling_schedules {workspace file name} [schedule ID]
    dh_hl list_child_schedules {workspace file name} {idea ID}
    dh_hl list_equal_schedules {workspace file name} [schedule ID]

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


### Force Parent Idea Tool

    dh_hl force_parent_idea {workspace file name} {idea ID} [schedule ID]

Adds the referenced schedule node as a child and the canonical
schedule of the referenced idea node.

This fails if:
* The referenced schedule node is not a root node.
* The referenced idea node already has a canonical schedule.
* The new edge would cause a tree structure invariant violation.

Rarely needed, mostly for when a new root node was created and you regret it.


### JSON Schedule Info Tool

    dh_hl json_schedule_info {workspace file name} [schedule ID]

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

    dh_hl json_idea_info {workspace file name} {idea ID}

Prints the state of the referenced idea node as a JSON object, with key/value pairs

* `id`: full ID of node

* `parent`: string, full ID of parent schedule

* `children`: list of strings, each a full ID of a child node

* `proposal_name`: string

* `proposal_text`: string

* `canonical_schedule`: null if no canonical schedule, otherwise string full ID of the canonical schedule

* `importance`: number if finite, null for negative infinity


### Fix Canonical Tool

    dh_hl fix_canonical {workspace file name} {idea ID}

After a merge conflict, the referenced idea node's canonical schedule may
record two competing IDs. This tool resolves that by modifying the catalog
graph so that:

* The older canonical schedule becomes the canonical schedule
  of the referenced idea node.
* That canonical schedule gains a new child idea node whose
  canonical schedule is the newer of the two competing schedules.
* The new child idea node has proposal name `fix_canonical_{timestamp}`
  and a proposal text noting it was auto-generated by `fix_canonical`.


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
