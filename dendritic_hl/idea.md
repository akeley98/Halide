# Dendritic Halide Harness -- Harness for agentic Halide scheduling

The process of scheduling Halide code -- whether by hand or automated -- is often a sort of tree search.
Schedules evolve over time into other schedules, and some plans don't work out and get back-tracked.
What I want is a system that stores a "catalog" of schedules, organized into a historic tree structure
(techincally a DAG, in some cases).

My goals for the Dendritic Halide Harness (`dendritic_hl.py`, shortened as `dh_hl`) are:

* **Long term memory device:** Give agents a system for long-term
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

* **Maximize Compatibility with Source Control:**
  "transparent"-ish on-disk state for catalog, designed to minimize merge conflicts.
  This is also why I say "catalog" and not "repository"

* Implemented in **one Python 3 file** for now even though I liketh it not

* In the end, this will only be a sketchy prototype, as I'm a lowly intern
  with not that much time in the grand scheme of things.


## Conceptual State

Each C++ file in the **workspace** (files outside the catalog) that has
ever been passed to `dh_hl` gets an associated catalog stored in a directory next to it.
The catalog is a bipartite DAG consisting of:

* **Schedule Nodes:** Holds C++ generator file and associated benchmarking information and commentary.
  May have 0 or more "idea nodes" as parents (but usually exactly 1, so the history is a tree).
  May have 0 or more idea nodes as children as well only if this is a major schedule (to be defined).
  The schedule is embedded with a UTC wall time timestamp.

* **Idea Nodes:** Holds a reference to exactly 1 parent schedule node,
  and includes a text proposal of how to further modify the schedule.
  The child schedule nodes are attempts of implementing the idea.
  Up to one of the child schedules is the idea node's **canonical schedule**.

Furthermore, there's a special **current idea state**
indicating which idea node the workspace file it is to be parented to
(or none at all).

A schedule node is a **root node** if it has 0 parents.

A schedule node is a **major schedule** if it is a root node or it is a canonical schedule of some idea node.

A schedule node is a **minor schedule** if it's not a major schedule.
The point of this is mainly to track "failed or flawed attempts" to implement the idea.
The "core state" of the catalog is the network of major schedules.
Minor schedules should not store interesting variations on schedules;
this should be done by adding a legitimate child idea node.


### DAG Consistency Requirements

* The parent of an idea node must be a **major schedule**.

* The parent schedule nodes of an idea must all have embedded
  timestamps strictly lower (in the past) relative to each of the
  child schedule nodes.


### Timestamp Format

Always use UTC time, format with `strftime("%Y-%m-%dT%H%M%S.%fZ")` (microsecond precision).
These are sortable lexicographically, assuming the year is between 1000 and 9999 CE.
We assume these increase monotonically even though leap seconds screw us.


### Hash Format

sha256, lowercase hex digits.


### Schedule Node State

* **C++ source code**

* **UTC wall time timestamp:** timestamp of when the schedule node was created.

* **Schedule Node Full ID:** `{timestamp}_{hash}` where `hash` is that of the stored C++ code encoded as UTF-8.
  Exactly 90 characters.

* **Edges:** 0 or more parent idea nodes, 0 or more child idea nodes,
  referenced by child node full ID (not yet defined).

* **Compilation Result:** C++ compiler error, Halide compiler error, or success.

* **Benchmark Result Files** JSON format

* **Generator Parameter Value Advice:** JSON format

* **Commentary Files:** Contains timestamp, optional importance value, commentary text.
  For minor schedules, should be used to explain what went wrong with this implementation attempt.
  For major schedules, should be used as a post-mortem, or commentary on the effectiveness of the change
  implemented from the idea.
  The harness doesn't enforce these "shoulds"


### Idea Node State

* **Proposal Name:** String of length in [1, 72], containing only alphanumeric characters and underscore.

* **Edges:** Exactly one parent schedule node, any number of child schedule nodes, referenced by schedule ID.

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


## Catalog Directory State

Each `*.cpp` or `*.cc` or `*.c++` file in the workspace has a corresponding
`*.dh_hl` "catalog directory" next to it, if it's ever input to `dh_hl`.

The top-level catalog directory contains sub-directories

* `bin`
* `idea`
* `sch`

and files

* `current_idea.txt`
* `.gitignore`, ignores `bin` directory


### Schedule Nodes on Disk

Each schedule node is stored in a `sch/{id}` subdirectory of the catalog directory.
This contains files and directories:

* **C++ source code:** `generator.cpp`

* **UTC wall time timestamp:** derived from full ID

* **Edges:** `parent/` and `child/` directories.
  Each edge to a parent idea node is stored as an empty `parent/{parent id}` file.
  Each edge to a child idea node is stored as an empty `child/{child id}` file.

* **Compilation Result:** `compile.txt`, holding `c++ error`, `halide error`, or `success`.

* **Benchmark Result Files:** store in `bench/{hostname}_{timestamp of benchmark}.json`

* **Generator Parameter Value Advice:** store in `param/{timestamp of advice}.json`

* **Commentary Files:** store in `comment/{timestamp of commentary}.txt` if no importance value,
  otherwise `comment/{timestamp of commentary}_{importance}.txt`.
  Contents are just the text of the commentary.

*Merge risk:* (unlikely) incoming different benchmarks, advice, or commentaries with the same timestamp.
No automatic fix provided.

*Merge risk:* Undetected violations of DAG consistency timestamp requirement.


### Idea Nodes on Disk

Each idea node is stored in a `idea/{id}` subdirectory of the catalog directory.
This contains files and directories:

* **Edges:** `child/` sub-directory;
  each edge to a child schedule node is stored as an empty `child/{child id}` file.
  Note the proposal name and parent schedule node are implied from this idea node's full ID.

* **Proposal Text** `proposal.txt`
  *Merge risk:* problem if two branches had the same proposal name and different proposal text.
  No automatic fix provided.

* **Canonical Schedule:** If there is one,
  its full ID plus a newline are written in `canonical.txt`.
  File doesn't exist if there's not yet a canonical schedule.
  *Merge risk:* Different IDs in incoming `canonical.txt`.
  Fix with `fix_canonical` tool.


### Current Idea State on Disk

Stored in `current_idea.txt`, single line with trailing newline holding

* `dendritic_hl_root({timestamp})` to encode the "no current idea" state

* `dendritic_hl_idea({idea node full ID})` to encode the "some current idea" state

*Merge risk:* Fix with `fix_current_idea` tool.
Ignore all lines except those parsing in the above 2 forms.


### Efficiency

This is not a super elegant format, which is kind of abusing the file system.
For a production implementation, I should probably stop creating thousands of content-free files.
Furthermore the `sch/` and `idea/` directories will end up becoming large,
requiring `O(n)` time for most tools.

It's mainly my goal of avoiding difficult git merge conflicts that yielded this design,
as creating separate files will not conflict, but editing a single "edge list" file will.

A production implementation would probably require a more efficient graph format,
along with tools for automatically resolving merge conflicts.

This design is risky in light of Windows traditional `MAX_PATH=260` limit,
which Python 3 is compiled to work around.
Mac limit is `1024` characters.


## Full and Short IDs

The IDs previously defined for idea and schedule nodes are the full IDs.
Only full IDs are stored in the catalog, because they are stable over time.
For convenience, short IDs are preferred almost everywhere else instead.

**Idea Node Short ID:** `{proposal name prefix}.{N}`, `N` an integer.

This references an idea node as follows:

* Consider only the list of idea nodes whose proposal names start with `proposal name prefix`.

* Sort this list by the timestamp of the parent schedule node, breaking ties by sorting by the full idea node ID.

* Select the `N`-th node, 0-indexed.

**Schedule Node Short IDs:** multiple formats

* `{idea node short id}` (alone) references the canonical schedule of the idea node.

* `{idea node short id}.{N}` references the `N`-th child schedule node of the idea node,
  0-indexed, sorted by schedule node ID.
  Because the schedule node ID starts with a timestamp,
  this is basically chronological order.

* `{hash prefix}` references the sole schedule node whose hash starts with the given prefix.
  Error if this is ambiguous.

All but the hash-based form "embed the parent idea node" of the reference schedule ID.

### Short ID input and output

Note short IDs (except the hash prefix form) are distinguished by containing at least one `.`

Whenever tools output IDs for nodes, they should output short IDs whenever possible.
For schedule nodes with:

* **One parent idea node:** Prefer short IDs that embed the parent idea node
* **No or multiple parent idea nodes:** Prefer hash-based IDs unless stated otherwise;
  fall back to full ID when truly required (no hash prefix is unambiguous).

Figure some heuristics for creating reasonable short IDs.

TODO: may add an override for this
(so use a helper function to format short IDs,
so there's a common place where this override can be implemented)


## Tools

In all tools, the schedule and idea IDs implicitly refer to nodes in the catalog associated with the workspace file.
If the workspace file doesn't exist, the tool reports an error, except for the restore tool.
If the workspace file exists but the catalog directory doesn't,
then an error is reported for read-only commands;
otherwise, the catalog directory is implicitly created.

`[parameter]` means an optional parameter.

`[schedule ID]`, if not given explicitly, implies the unambiguous schedule node ID given by `dh_hl status`,
or an error if no unambiguous schedule node ID would be given.
It is rarely needed to pass this, unless you're doing some heavy-handed modification of the catalog state.

The "current idea node" is nothing, if the current idea state encodes "no current idea",
and otherwise the idea node referenced by the "some current idea" state.


### Help Tool

    dh_hl help [command]

List commands briefly if no `[command]` given, otherwise give description of the named command.


### Status Tool

    dh_hl status {workspace file name}

This is a purely read-only command.

If there's no catalog directory, advise `dh_hl new_root {workspace file name}` and exit.
Otherwise,

* Give the current idea state

* Give the **unambiguous schedule node** ID (defined soon), if it exists.
  If it doesn't, issue the warning:

    AGENTS: If this is the first time editing this file this session,
    DO NOT PROCEED as the catalog may have been left in an inconsistent state,
    unless you have been advised otherwise. (ambiguous schedule ID)

The unambiguous schedule node is:

* If the current idea state encodes "no current idea",
  and there is a schedule node whose timestamp and hash matches the
  current idea state timestamp and workspace file hash respectively,
  then the unambiguous schedule node is that schedule node.

* If there exists a current idea node, and it has a child schedule whose hash
  matches the workspace file hash, then the unambiguous schedule node
  is that child schedule node.
  If there's multiple such nodes:
    - Give the canonical schedule, if it's one of the choices.
    - Warn & show the list of such nodes' IDs otherwise; no unambiguous schedule node in this case.
  If the output is in short ID form, embed the ID of the current idea node in it.

* Doesn't exist otherwise.

**Rationale:** a workspace file is in "consistent state"
when it unambiguously corresponds to a schedule node.
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

Copy the schedule node's C++ schedule to the workspace file,
and update the current idea node state as follows,
depending on the number of parent idea nodes of the referenced schedule node.

* **No parents:** set to "no current idea" state, embedding the timestamp of the schedule node.

* **One parent:** set to "some current idea" state, embedding the ID of the parent idea node.

* **Multiple parents:** if the schedule ID used a short ID that embeds an idea node,
  set to "some current idea" state, embedding the ID of said idea node.
  Otherwise, issue an error, with suggested legal short IDs for this schedule node,
  one each for each parent idea node.


### Build Tool

    dh_hl build {workspace file name} [parameters]

TODO


### Benchmark Tool

    dh_hl benchmark {workspace file name} [parameters]

TODO


### Canon Tool

    dh_hl canon {workspace file name}

Set the canonical schedule of the current idea node to the schedule node named by `dh_hl status`.
This is a schedule node that holds a copy of the workspace schedule.

Requirements:
    * Current idea node must exist
    * `dh_hl status` must give an unambiguous schedule node ID
    * Referenced schedule must have a `success` compilation state
    * The current idea node must not already have a canonical schedule

If the command fails due to the last requirement:
    * Advise this was already done if the schedule node is already the canonical schedule.
    * Advise the `dh_hl new_idea` and `dh_hl set_idea` tools otherwise.


### Comment Tool

    dh_hl comment {workspace file name} {commentary file} [schedule ID]

Add a new commentary file to the referenced schedule node,
contents copied from the passed `commentary file`.
`-` for stdin.
The commentary file has no importance value.


### Comment With Importance Tool

    dh_hl comment_importance {workspace file name} {commentary file} {importance} [schedule ID]

Like the `comment` tool but with the addition of the importance value.


### Advise Parameters Tool

    dh_hl advise_parameters {workspace file name} [parameters]

TODO


### New Root Tool

    dh_hl new_root {workspace file name}

Hash the file and look for existing schedule nodes with the same hash.
Error if so, giving IDs of all such schedule nodes.

Otherwise, create a new root node schedule node that contains a
copy of the workspace file, and set the current idea state to
"no current idea" embedding the timestamp of the new schedule node.


### Set Idea Tool

    dh_hl set_idea {workspace file name} {idea ID}

Update the current idea state to "some current idea",
embedding the given idea node ID.


### New Idea Tool

    dh_hl new_idea {workspace file name} {proposal name} {proposal file} [schedule ID]

Add a new child idea node to the referenced schedule node.
The schedule node must be a major schedule.
`-` for the proposal file means read from stdin.

Error if this would cause an ID collision (i.e. the proposal name is already used).

Does not give back the ID of the new idea node, because it may be invalidated later.


### List Ideas Tool

    dh_hl list_ideas {workspace file name} [schedule ID]

Error if the referenced schedule node is not a major schedule.

For each child idea node of the referenced schedule node, print 3 lines:

* The ID of the idea node
* The proposal name (indent by 2)
* The first up-to 72 characters of the first line of the proposal text (indent by 2)


### View Idea Tool

    dh_hl view_idea {workspace file name} {idea ID}

Print the referenced idea node's

* proposal name
* full proposal text
* list of child schedule IDs, one line each


### Force Parent Idea Tool

    dh_hl force_parent_idea {workspace file name} {maximum parent ideas} {idea ID} [schedule ID]

Set the referenced schedule node to be the canonical schedule of the referenced idea node.
(implies adding an edge).

This fails if:
* The referenced idea node already has a canonical schedule
* The referenced schedule node would end up with more than `maximum parent ideas`-many parent idea nodes.

Rarely needed, mostly for when a new root schedule was created and you regret it.
Hence `maximum parent ideas`, which should be 1 for this use case.


### JSON Schedule Info Tool

    dh_hl json_schedule_info {workspace file name} [schedule ID]

Print out state of the referenced schedule node as a JSON object, with key/value pairs

* `id`: full ID of node

* `parents`: list of strings, each a full ID of a parent node

* `children`: list of strings, each a full ID of a child node

* `source`: string, C++ source code

* `timestamp`: string, timestamp

* `hash`: string

* `compile`: string, `compile.txt` result

TODO benchmark, generator parameters, commentary


### JSON Idea Info Tool

    dh_hl json_idea_info {workspace file name} {idea ID}

Print out state of the referenced idea node as a JSON object, with key/value pairs

* `id`: full ID of node

* `parent`: string, full ID of parent schedule

* `children`: list of strings, each a full ID of a child node

* `proposal_name`: string

* `proposal_text`: string

* `canonical_schedule`: null if no canonical schedule, otherwise string full ID of the canonical schedule

* `importance`: number


### History Tool

    dh_hl history {workspace file name} [schedule ID]

TODO

When following edges, check the DAG consistency rules.
So we are guaranteed not to end up in an infinite loop even if the catalog state is cooked.


### Fix Canonical Tool

    dh_hl fix_canonical {workspace file name} {idea ID}

Scan the file storing the canonical schedule ID for the referenced idea node.
There should be two IDs (from the merge conflict).
Modify the catalog graph so that

* The older canonical schedule becomes the canonical schedule
  of the referenced idea node.
* Said canonical schedule has a new child idea node added whose
  canonical schedule is the newer canonical schedule.
  The proposal name and text indicates a resolved merge conflict.


### Fix Current Idea Tool

    dh_hl fix_current_idea {workspace file name}

TODO

TODO Workspace file must at least pass the C++ compilation step correctly.


## Tool Safety Requirements

Tools should assume the catalog directory will not be modified while the tool is running.
e.g. the tool may cache the list of schedule/idea node IDs once.

Tools can assume sha256 collisions never happen.

Tools NEVER overwrite or modify existing files, except for:

* Workspace C++ files
* `current_idea.txt`
* `canonical.txt` for `fix_canonical` tool
* `current_idea.txt` for `fix_current_idea` tool
* (TODO any exceptions I forgot?)

Accordingly, use `"x"` mode or equivalent when creating new files.

We furthermore must make all changes to the catalog atomic as much as possible.
For any tool run, record a list of new files created, and NEW idea node or schedule node directories created (not existing directories touched).
Use a common helper for this.

If the tool fails, an `atexit` handler will run that deletes all those recorded new files and directories.
It is OK to ignore node sub-directories like `child/`; I just care that there are no half-baked new idea node or schedule node directories left behind.

The "new files" don't include the workspace C++ file and the special case overwritten files.
NEVER delete the workspace C++ file.
Defer overwriting files as the FINAL step of tools.

Make SIGQUIT raise `KeyboardInterrupt` and try to prevent `KeyboardInterrupt` and exceptions from stopping the `atexit` handler.

Remember to check DAG consistency requirements whenever adding new edges.
Strongly advised to use a common helper function for this.


## Parameter Value JSON Format

TODO


## Benchmark JSON Format

TODO
