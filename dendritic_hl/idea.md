# Dendritic Halide Harness -- Harness for agentic Halide scheduling

The process of scheduling Halide code -- whether by hand or automated -- is often a sort of tree search.
Schedules evolve over time into other schedules, and some plans don't work out and get back-tracked.
What I want is a system that stores a "catalog" of schedules, organized into a historic tree structure.

My goals for the Dendritic Halide Harness (`dendritic_hl.py`, shortened as `dh_hl`) are:

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

* **Maximize Compatibility with Source Control:**
  "transparent"-ish on-disk state for catalog, designed to minimize merge conflicts.
  This is also why I say "catalog" and not "repository".

* Implemented in **one Python 3 file** for now even though I liketh it not

* In the end, this will only be a sketchy prototype, as I'm a lowly intern
  with not that much time in the grand scheme of things.


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
indicating which idea node the workspace file it is to be parented to
(or none at all).

A schedule node is a **root node** if it has 0 parents.

A schedule node is a **major schedule** if it is a root node or it is a canonical schedule of some idea node.

A schedule node is a **minor schedule** if it's not a major schedule.
The point of this is mainly to track "failed or flawed attempts" to implement the idea.
The "core state" of the catalog is the network of major schedules.
Minor schedules should not store interesting variations on schedules;
this should be done by adding a legitimate child idea node.


### Tree Structure Requirements

* The parent of an idea node must be a **major schedule**.

* Each node must have timestamp strictly greater than
  (in the future relative to) its parent node's timestamp.
  Ensures no cycles.

These rules only have to be checked to the extent that tools
will not add new violations of the requirements.


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

* **Edges:** 0 or 1 parent idea nodes, 0 or more child idea nodes.
  *Alternate design* had multiple parent ideas possible (DAG not tree),
  which was helping the "git compatibility" goal,
  but just raised too many tough cases for a prototype.

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

* **Proposal Name:** String of length in [1, 72], containing only alphanumeric characters and underscore.

* **UTC wall time timestamp:** timestamp of when the idea node was created.

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


## Catalog Directory State

Each `*.cpp` or `*.cc` or `*.c++` file in the workspace has a corresponding
`*.dh_hl` "catalog directory" next to it, if it's ever input to `dh_hl`.

The top-level catalog directory contains sub-directories

* `bin`
* `idea`
* `sch`

and files

* `current_idea.txt`
* `.gitignore`, ignores `bin`


### Schedule Nodes on Disk

Each schedule node is stored in a `sch/{id}` subdirectory of the catalog directory.
This contains files and directories holding state:

* **C++ source code:** `generator.cpp`

* **UTC wall time timestamp:** derived from full ID

* **Edges:** `parent.txt` holds the full ID of the parent idea node plus a newline,
  unless this schedule node is a root node, in which case `parent.txt` doesn't exist.
  Edges to child idea nodes are *derived state*.
  Scan the `idea` nodes directory (to be defined)
  for nodes whose full IDs have the correct `parent id`.

* **Result:** `result.txt`,
  holding `c++ error`, `halide error`, or `success`.
  The default value is `c++ error`.

* **Benchmark Result Files:** store in `bench/{hostname}_{timestamp of benchmark}.json`

* **Commentary Files:** store in `comment/{timestamp of commentary}.txt` if no importance value,
  otherwise `comment/{timestamp of commentary}_{importance}.txt`; importance formatted base-10 like `%d`.
  Contents are just the text of the commentary.

*Merge risk:* `parent.txt` merge conflict if two branches retroactively parented
a root schedule node to two different idea nodes.
No automatic fix provided -- this power should be used very sparingly anyway.

*Merge risk:* (unlikely) incoming different benchmarks, advice, or commentaries with the same timestamp.
No automatic fix provided.

*Merge risk:* Could undetected violations of tree structure timestamp requirements happen?


### Idea Nodes on Disk

Each idea node is stored in a `idea/{id}` subdirectory of the catalog directory.
This contains files and directories holding state:

* **UTC wall time timestamp:** `timestamp.txt`, timestamp string plus newline

* **Edges:** Both the parent and children are *derived state*.
  Parent schedule node is derived from this idea node's ID.
  Child schedule nodes are derived by walking `sch` for schedule nodes
  who have this idea node as their parent.
  **The tools DON'T** proactively do this walk; they must do it lazily,
  once, only if this information is actually needed.

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

After a git merge conflict, there may be multiple lines encoding
competing state values, plus extra cruft from git.
We will use a simplistic algorithm where every line not parsing
in one of the above two forms is assumed to be cruft.

The parser for the current idea state must be robust to this merge
conflict case. When this happens, report the competing state values
and suggest the `new_root` tool.

This info is part of an error message or the formatted output of the
`status` tool.

FUTURE: The `new_root` tool is a bare-minumum recovery strategy
from merge conflicts. A production version of Dendritic Halide maybe
can offer better tools, but at some point we're re-inventing git.


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

**Idea Node Short ID:** `{proposal name prefix}.{N}`, `N` a base-10 integer.

This references an idea node as follows:

* Consider only the list of idea nodes whose full IDs start with `proposal name prefix`.

* Select the `N`-th node, 0-indexed.

**Schedule Node Short IDs:** multiple formats

* `{idea node short id}` (alone) references the canonical schedule of the idea node.

* `{idea node short id}.{N}` references the `N`-th child schedule node of the idea node,
  0-indexed, sorted by schedule node full ID.
  Because the schedule node ID starts with a timestamp,
  this is basically chronological order.
  Only this short ID form requires an expensive walk of the full graph structure,
  and it is the uncommon case, only for minor schedules.

* `{hash prefix}` references the sole schedule node whose hash starts with the given prefix.
  Error if this is ambiguous.

All but the hash-based form "embed the parent idea node" of the reference schedule ID.

### Short ID input and output

Note short IDs (except the hash prefix form) are distinguished by containing at least one `.`

Whenever tools output IDs for nodes, they should output short IDs whenever possible.
For schedule nodes with:

* **No parent idea node:** Prefer hash-based IDs unless stated otherwise;
  fall back to full ID when truly required (no hash prefix is unambiguous).
* **One parent idea node:** Prefer short IDs that embed the parent idea node.

Figure some heuristics for creating reasonable short IDs.

FUTURE: may add an override for this
(so use a helper function to format short IDs,
so there's a common place where this override can be implemented)


## Tools

In all tools, the schedule and idea IDs implicitly refer to nodes
in the catalog associated with the workspace file.

If the workspace file doesn't exist, the tool reports an error, except for the restore tool.

If the workspace file exists but the catalog directory doesn't,
then an error is reported for read-only commands;
otherwise, the catalog directory is implicitly created.

`{...}` (curly brackets) means a mandatory argument.

`[...]` (square brackets) means an optional argument.

`[schedule ID]`, if not given explicitly,
implies the unambiguous schedule node ID that would be given by `dh_hl status`,
or an error if no unambiguous schedule node ID would be given.
It is rarely needed to pass this, unless you're doing some heavy-handed modification of the catalog state.

The "current idea node" is nothing, if the current idea state encodes "no current idea",
and otherwise the idea node referenced by the "some current idea" state.
Commands that explicitly edit the current idea state must not error out
due to errors in the existing `current_idea.txt`.


### Help Tool

    dh_hl help [command]

List commands briefly if no `[command]` given, otherwise give description of the named command.


### Status Tool

    dh_hl status {workspace file name}

This is a purely read-only command.

If there's no catalog directory, advise `dh_hl new_root {workspace file name}` and exit.
Otherwise, the tool tries to find a schedule node that already holds the workspace file
and give basic information on the current catalog state.

**Search:**

Hash the workspace file and look for schedule nodes with matching hashes.

If none exist, the status is "workspace inconsistent, unknown schedule".

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
(b) has its parent idea node matching the onc embedded in the current idea state,
*then* that schedule node is the **unambiguous schedule node**
and the status is "workspace consistent".

Otherwise, the status is "workspace inconsistent, unexpected current idea state".

**Outputs:**

* Give the current idea state
  (no current idea/some current idea/parse error/missing/etc.)
  Try to print errors cleanly in this case.

* Gives the status as one of
    - "workspace inconsistent, unknown schedule"
    - "workspace inconsistent, unexpected current idea state"
    - "workspace consistent"

* If the workspace is consistent, print the ID of the unambiguous schedule node.

* If the workspace is inconsistent, give the warning

        AGENTS: If this is the first time editing this file this session,
        this means the file was edited without correct harness tracking.
        DO NOT PROCEED, unless you have been advised otherwise.
        Likely causes include user action, and git checkouts / merges.


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

Copy the schedule node's C++ schedule to the workspace file,
and update the current idea node state as follows,
depending on the number of parent idea nodes of the referenced schedule node.

* **No parents:** set to "no current idea" state, embedding the timestamp of the schedule node.

* **One parent:** set to "some current idea" state, embedding the ID of the parent idea node.


### Build Tool

    dh_hl build {workspace file name} [parameters file]

This tool tries to compile the workspace file and add/update a schedule node for it.
There are four steps:

1. Find or create the edited schedule node
2. Compile the Halide binary
3. Conditionally update the result state of the edited schedule node
4. Print the ID of the edited schedule node

(1) The edited schedule node is:

* If `dh_hl status` would give an unambiguous schedule node,
  that schedule node is the one this tool edits.
* Otherwise, if there is no current idea node,
  give an error, and suggest the `set_idea` and `new_root` tools.
* Otherwise, add a new child schedule node to the current idea node
  holding a copy of the workspace file.

Note `new_root` shouldn't be used often, hence this tool doesn't try
to automate `new_root` and forces the user/agent to do it themselves
and think about whether that actually makes sense.

(2) Use the `bin` directory of the catalog directory as temporary storage.

Use the `ninja` build tool to
* compile the C++ workspace file to Halide generator
* run the Halide generator to get a Halide binary and `conceptual_stmt` file.
  Use `target=host-profile` and link to `RunGenMain` to finish a standalone binary.

[RunGenMain doc](https://halide-lang.org/docs/md_doc_2_run_gen.html)

Print the file name (in the `bin/`) directory of the `conceptual_stmt` file.
It can be overwritten by future builds.
Pipe the output `stdout` and `stderr` to the harness's `stdout` and `stderr`.

`gen_hist_host/build.ninja` provides an example of this two-step build.
But you will have to Google how to link in `RunGenMain`.

If the parameters file was given, it must hold a generator parameters JSON object
(described later).
Unpack the key/value pairs and pass them as generator parameters.
If not given, it's as if an empty generator parameters JSON object was given.

NB when executing the generator, each numeric parameter value must be
formatted with `%d` if it's a whole number, and with `%r` if not,
to ensure no floating point roundoff.

(3) The result state of the schedule node gets updated to one of:

* `c++ error`: couldn't even compile the C++ workspace file (worst)
* `halide error`: passed said step, but Halide generator exited unsuccessfully
* `success`: both steps exited successfully (best)

However, update the result state to the better of the previous and new value.
This is to account for how some generator parameter values may cause the
Halide generator to fail; doesn't mean the entire schedule is bad.

(4) Finally (after all other printing including the sub-processes),
print the ID of the edited schedule node.

This tool exits successfully iff no harness errors occurred
and all subprocesses succeeded. 

FUTURE: configurable Halide library location.
For now just define a magic constant `~/Halide/build/`
which will work on this computer at least.

FUTURE: switch to CMake if absolutely huge payoff would happen (I hate CMake).



### Profile Tool

    dh_hl profile {workspace file name} [parameters file]

This is like `dh_hl build` except
* The Halide binary is run with Andrew Adam's new profiler tool
  and the benchmark results are recorded.
* The parameters file may contain a list of generator parameters JSON object,
  with each parameter set profiled in turn.

The list of generator parameters JSON objects for the command is
* `[{}]`, if the parameters file was not given
* `[obj]`, if the parameters file encodes a single JSON object `obj`
* The parsed contents of the parameters file, verbatim, if it's already a list

Steps 2 and 3 of the `dh_hl build` command are modified to become a loop over this list.
Build the C++ to Halide generator once, then, for each object in the list,

* Generate the Halide binary from the generator (no `stmt` needed this time).
* Update result state of the edited schedule node as in `dh_hl build`, step 3.
* Run the binary with `--verbose --benchmarks=all --estimate_all` (FUTURE: `--estimate_all` isn't great)
* Add a new benchmark JSON object (documented later) to the edited schedule node's benchmarks set.

Don't fail irrecoverably if some builds fail.
Just skip it and move on.

Examples: see the `gen_hist_host` and `hist_run_rule` rules in `gemm_halide_test/build.ninja`.
But you will have to Google how to link in `RunGenMain`.

IMPORTANT: each benchmark run must monopolize the entire computer
(ignoring outside processes that we can't reasonably control).
Ergo, no parallelizing benchmarking, no compiling while benchmarking.

Implement the loop in Python; don't try to get `ninja` to build and
profile everything, even if theoretically possible with pools,
so I'm not paranoid when I have to inherit this software later.

This tool exits successfully iff no harness errors occurred
and no subprocesses exited unsuccessfully.


### Build/Profile Tool Future Work

FUTURE: allow benchmarking without the profiler.

FUTURE: specify input size / explicit inputs for profiling
that are passed through to `RunGenMain`.

FUTURE: allow alternative to Halide's random number generator,
since the buffer contents may have considerable impact on
performance for algorithms like atomic-increment histograms
(e.g. many 0s = lock contention, not observable for uniform distribution).

FUTURE: ask Andrew Adams to output JSON profiler output.
So we can put all the information away in the benchmark files.

FUTURE: GPU target.
More in general really we should just pass args through
to the Halide generator and RunGenMain.


### Canon Tool

    dh_hl canon {workspace file name}

Set the canonical schedule of the current idea node to the schedule node named by `dh_hl status`.
This is a schedule node that holds a copy of the workspace schedule.

Requirements:
    * Current idea node must exist
    * `dh_hl status` would give an unambiguous schedule node ID
    * Referenced schedule must have a `success` result state.
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


### New Root Tool

    dh_hl new_root {workspace file name}

Hash the file and look for existing schedule nodes with the same hash.
If any of them are major schedules, issue an error,
giving IDs of all such schedule nodes.

Otherwise, create a new root node schedule node that contains a
copy of the workspace file, and set the current idea state to
"no current idea" embedding the timestamp of the new schedule node.

This tool must succeed regardless of the contents of the current idea
state on disk. However, if parsing the existing file yielded multiple
encoded current idea states, have the additional effect of adding
commentary to the new schedule node in the form:

        dh_hl new_root tool: automated merge conflict recovery
        [one line for each encoded current idea state parsed,
        in any order and the same format as the current idea state file]

and with no importance value attached.
This is just a temporary "bare minimum" merge conflict resolution.


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

Gives back the ID of the new idea node.


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

Add some minimal formatting to make it look nice.


### Force Parent Idea Tool

    dh_hl force_parent_idea {workspace file name} {idea ID} [schedule ID]

Add the referenced schedule node to be a child and the canonical
schedule of the referenced idea node.

This fails if:
* The referenced schedule node is not a root node.
* The referenced idea node already has a canonical schedule.

Rarely needed, mostly for when a new root node was created and you regret it.


### JSON Schedule Info Tool

    dh_hl json_schedule_info {workspace file name} [schedule ID]

Print out state of the referenced schedule node as a JSON object, with key/value pairs

* `id`: full ID of node

* `parents`: list of strings, each a full ID of a parent node

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

Print out state of the referenced idea node as a JSON object, with key/value pairs

* `id`: full ID of node

* `parent`: string, full ID of parent schedule

* `children`: list of strings, each a full ID of a child node

* `proposal_name`: string

* `proposal_text`: string

* `canonical_schedule`: null if no canonical schedule, otherwise string full ID of the canonical schedule

* `importance`: number if finite, null for negative infinity


### History Tool

    dh_hl history {workspace file name} [schedule ID]

Walk the branch of the tree starting from the referenced schedule node.
Alternate between printing nodes and following edges from child to parent,
terminating after printing the root node.

When following an edge from child to parent node, check that
this edge follows the tree structure timestamp requirement.
So we are guaranteed not to end up in an infinite loop
even if the catalog state is cooked.

For each schedule node, print:

* Its ID
* For each commentary file,
  print its timestamp on one line,
  and print the first up-to 72 characters of the first line of the commentary text.

For each idea node, print in the same format as `dh_hl list_ideas`.
Try to recycle common code plz.

Add some minimal formatting to make it look nice.
Put conspicuous dividers between the info printed for each node.

FUTURE: use the `importance` stuff to filter to less info


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


## Tool Safety Requirements

Tools should assume the catalog directory will not be modified while the tool is running.
e.g. the tool may cache the list of schedule/idea node IDs once.

Tools can assume sha256 collisions never happen.

Tools NEVER overwrite or modify existing files, except for:

* Workspace C++ files
* `current_idea.txt`
* `result.txt`
* `canonical.txt` for `fix_canonical` tool
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

Remember to check tree structure requirements whenever adding new edges.
Strongly advised to use a common helper function for this.


## Tool Internal Design

TODO


## Generator Parameters JSON Object Format

TODO


## Benchmark JSON Format

TODO hostname, CPU count, runtime parsed from profile.

FUTURE: once profile tool accepts explicit input sizes etc.
we need to embed that in here.

FUTURE: ask Andrew Adams to output JSON profiler output.
So we can put all the information away in the benchmark files.
