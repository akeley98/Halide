# Implementation Notes for Dendritic Halide Harness (dh_hl)


## Catalog Directory State

Each `*.cpp` or `*.cc` or `*.c++` file in the workspace has a corresponding
`*.dh_hl` "catalog directory" next to it, if it's ever input to `dh_hl`.
The directory's name is the C++ filename with `.dh_hl` appended (e.g. `.cpp.dh_hl`)

The top-level catalog directory contains sub-directories

* `bin`
* `idea`
* `sch`

and files

* `current_idea_state.txt`
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

  *Alternate design* had multiple parent ideas possible (DAG not tree),
  which was helping the "git compatibility" goal (e.g. encode merge conflict resolution),
  but just raised too many tough cases for a prototype with questionable payoff.

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

*Merge risk:* `result.txt` conflict.
Unlikely but could happen due to committing a failed build that
later worked due to trying different generator parameters.
No automatic fix provided.

*Merge risk:* (unlikely) incoming different benchmarks, advice, or commentaries with the same timestamp.
No automatic fix provided.

*Merge risk:* Undetected tree structure invariant violations may happen
as a result of combining `force_parent_idea` in one branch and adding
new child idea nodes and schedules in another.
It's possible for the two operations to be legal separately, but not together.


### Idea Nodes on Disk

Each idea node is stored in a `idea/{id}` subdirectory of the catalog directory.
This contains files and directories holding state:

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

Stored in `current_idea_state.txt`, single line with trailing newline holding

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

FUTURE: The `new_root` tool is a bare-minimum recovery strategy
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


### Status Tool -- Implementation Details

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
(b) has its parent idea node matching the one embedded in the current idea state,
*then* that schedule node is the **unambiguous schedule node**
and the status is "workspace consistent".

Otherwise, the status is "workspace inconsistent, unexpected current idea state".

**Outputs:**

* Give the current idea state
  (no current idea/some current idea/parse error/missing/etc.).
  Try to print errors cleanly if something is wrong with the state on disk.
  If the current idea node exists, print the status of its canonical schedule (none, or ID of it).
  If the current idea state is syntactically correct but references a nonexistent idea node,
  advise of that too (defensive helpfulness, in case we want the current idea state out of git)

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


### Build Tool -- Implementation Details

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

**Build driver split (decided):** use `ninja` only for the param-independent
steps, and drive everything param-dependent from Python `subprocess`:
* Ninja builds phase 1 (the C++ workspace file -> Halide generator executable)
  and compiles `RunGenMain.o`. These are built ONCE and don't depend on
  generator parameters.
* Python drives the param-dependent phases directly with `subprocess`
  (serially, no parallelism): run the generator to emit outputs (phase 2),
  link the standalone binary (phase 4), and, for `profile`, run the benchmark.
  For `profile` this per-param-set work is a Python `for` loop; don't push the
  loop into ninja (see the Profile Tool's explicit note).

The steps performed are:
* compile the C++ workspace file to a Halide generator executable (ninja)
* run the generator to emit the AOT static library, header, `registration.cpp`,
  and both the plain `.stmt` and `conceptual.stmt` files, using
  `target=host-profile` (Python)
* link `RunGenMain` against the generated `registration.cpp` + static library
  to finish a standalone benchmarkable binary (Python)

**Generator name.** `GenGen` still requires `-g <name>` even when only one
generator is registered, but under the single-generator assumption (see Goals)
the name is discovered automatically: run the generator executable with **no**
`-g`, and it errors out listing `available Generators are:` followed by the sole
registered name; scrape that single name and pass it as `-g`. Then use a
**fixed** output basename (e.g. `-f dh_hl_gen`), so all emitted filenames
(`dh_hl_gen.a`, `dh_hl_gen.registration.cpp`, `dh_hl_gen.conceptual.stmt`, ...)
are independent of the generator's registered name. This whole path was tested
end-to-end against the local Halide build.

If the `available Generators are:` list contains anything other than exactly
one name (zero, or two or more), the tool reports a harness error and stops.
This check happens *after* phase 1 (the C++ compile to a generator exe) has
already succeeded, so it is a workspace-authoring problem (the single-generator
assumption is violated), not a build outcome to catalogue: do **not** record a
result-state update for it, and leave any pre-existing result on a reused node
untouched. The error should name the generators found so the user can fix the
workspace file. This enforces the assumption rather than silently picking one.

[RunGenMain doc](https://halide-lang.org/docs/md_doc_2_run_gen.html)

Print the file names (in the `bin/` directory) of the emitted `.stmt` and
`conceptual.stmt` files.
They can be overwritten by future builds.
Pipe the output `stdout` and `stderr` to the harness's `stdout` and `stderr`.

See the **Reference Build Commands** subsection below for a tested,
contained example of the full generator + `RunGenMain` build.

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
which will work on David's MacBook at least.

FUTURE: switch to CMake if absolutely huge payoff would happen (I hate CMake).


### Profile Tool -- Implementation Details

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

Steps 2 and 3 of the `dh_hl build` command are modified to become a loop over this list.
Build the C++ to Halide generator once, then, for each object in the list,

* Generate the Halide binary from the generator (no `.stmt`/`conceptual.stmt` needed this time).
* Update result state of the edited schedule node as in `dh_hl build`, step 3.
* Run the binary with `--verbose --benchmarks=all --estimate_all` (FUTURE: `--estimate_all` isn't great)
  and with `HL_PROFILER_JSON_OUTPUT=...` to get profiler JSON data out.
* Add a new benchmark JSON object (documented later) to the edited schedule node's benchmarks set.
  NOTE: this is unlikely but a Claude reviewer of the document recommended
  busy waiting for the timestamp to change, so 2 benchmarks in the same microsecond
  won't cause a benchmark name collision.

Don't fail irrecoverably if some builds fail.
Just skip it and move on.

See the **Reference Build Commands** subsection under the Build Tool for the
tested build/link recipe. For `profile`, keep the generator executable from
phase 1 and re-run the emit + link + benchmark steps for each parameter set.

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

FUTURE: GPU target.
More in general really we should just pass args through
to the Halide generator and RunGenMain.


## Tool Safety Requirements

Tools should assume the catalog directory will not be modified while the tool is running.
e.g. the tool may cache the list of schedule/idea node IDs once.

Tools can assume sha256 collisions never happen.

Tools NEVER overwrite or modify existing files, except for:

* Workspace C++ files
* `current_idea_state.txt`
* `result.txt`
* `canonical.txt` for `fix_canonical` tool
* `parent.txt` for `fix_canonical` tool (see note below)

Accordingly, use `"x"` mode or equivalent when creating new files.

*`parent.txt` overwrite exception (`fix_canonical`):* the `fix_canonical`
description re-parents the *newer* competing canonical schedule so it becomes
the canonical schedule (hence a child) of a freshly created resolution idea
node. Because a schedule's parent edge is stored solely in its `parent.txt`,
and the newer schedule already has a `parent.txt` pointing at the original
idea, honoring that description requires **overwriting** the newer schedule's
`parent.txt`. This is the sole tool permitted to overwrite `parent.txt`, and
like the other overwrites it is deferred and not rolled back. (This is a
deliberate exception to the otherwise strict anti-`parent.txt`-overwrite
stance; the whole file layout exists to avoid `parent.txt` churn, but this
rare recovery tool is the documented escape hatch.)

We furthermore must make all changes to the catalog atomic as much as possible.
For any tool run, record a list of new files and new directories created
(not existing directories touched).
Use a common helper for this.

If the tool fails, an `atexit` handler will run that deletes all those recorded new files and directories
(disable the `atexit` handler as the final step before successful exit,
so we don't delete new files when the tool succeeds!)
Again, be very very careful about new vs. existing directories.
Don't delete any directories you didn't create.

*What counts as "the tool fails" (rollback) vs. a catalogued bad outcome:*
The rollback is for **harness/logic failures** -- an unexpected exception, a
pre-flight validation error, an environment problem -- i.e. cases where the
in-memory changes are incomplete or untrustworthy and must be undone. It is
**not** triggered by a subprocess reporting a bad *build outcome*. Recording a
schedule node whose C++ failed to compile (`c++ error`) or whose Halide
generator failed (`halide error`) is the build/profile tool **succeeding at
its cataloguing job** (recall the goal: "all C++ source code ever compiled
will be catalogued"). Concretely, for `build`/`profile`:

* Pre-flight validation problems (e.g. no current idea node, unresolved ID)
  are raised **before** any node is created, so rollback has nothing to undo.
* A `c++ error` / `halide error` build outcome, and the "generator list is not
  exactly one name" harness error, are **not** raised as rollback-triggering
  exceptions. They are recorded in memory (for the generator-count case,
  simply *skip* the result-state update per the Build Tool), the single
  end-of-tool flush + commit runs so the node **persists**, and only then does
  the process exit with a nonzero status to signal the failure to the caller.

So flushing stays a single step strictly separated from the main logic (per
the Tool Internal Design); "did a subprocess fail" affects only the process
**exit code**, never whether we flush.

**OVERRIDING SAFETY RULE:** never use "recursive directory delete" functions.
If you delete files and directories in the opposite order as creation,
a directory will be empty when deleted, so `os.rmdir` will work.
This rule prevents PERMANENT DAMAGE from bugs (like deleting my `home`).

The "new files" don't include the workspace C++ file and the special case overwritten files.
NEVER delete the workspace C++ file.
Defer overwriting files as the FINAL step of tools, because we don't roll back these overwrites.
So overwriting as late as possible minimizes the risk of crashing after the overwrite.

Make SIGQUIT raise `KeyboardInterrupt` and try to prevent `KeyboardInterrupt` and exceptions from stopping the `atexit` handler.
(But don't get stuck in an infinite loop if an `OSError` happens).

Remember to check tree structure invariants whenever adding new edges.
You are responsible for ensuring the tree structure invariants are
not violated even if it's not explicitly spelled out as a failure mode of the tool.
Hence it's strongly advised to use a common helper function for checking and adding edges.


## Tool Internal Design

Obviousness and idiot-proofing are priorities for this prototype since
this design may evolve quickly and isn't meant to scale to production uses.
I'd like to have most of the harness code working with an in-memory representation
that's fairly 1:1 with the conceptual state.

Each tool execution is short-lived and breaks into multiple phases

* Lazily load the needed parts of the catalog to memory
* Modify state in-memory (can be interleaved with lazy loads)
* Flush changes to the catalog directory, in two sub-phases,
  write all new files, then overwrite existing files.

I don't want any file opened or parsed more than once.

There is a top-level `Catalog` object, owning
* A single `CurrentIdeaState` object
* A `Dict[str, IdeaNode]`: idea nodes by full ID
* A `Dict[str, ScheduleNode]`: schedule nodes by full ID

Each object
* is accessed with getters and setters
* has initially empty state, and is lazily initialized from disk when needed by getters
* is dirtied when modified, or upon creation if it's not loaded from disk;
  do this in each setter and non-load-from-disk `__init__` path;
  DON'T ever expect outside code to dirty an object manually!
* has `flush_new` and `flush_overwrite` callbacks that implement
  the "write new files" and "overwrite existing files" steps of flushing
  state to disk
* may own lazily-created sub-objects corresponding to some piece of conceptual state;
  for example, a schedule node object owns commentary sub-objects.

Furthermore, each node contains a lazily-initialized derived list of
child nodes. This is **all-or-nothing** for each category of node.
When the tool needs the child nodes of a schedule node,
all the idea nodes are loaded and walked to initialize all schedule nodes' children.
Same, with idea and schedule nodes swapped.

Dirty objects get added to a `Dict[int, object]` dict where the key is the object's `id`.
All objects in here get flushed just before the tool exits.
* Each file on disk is strictly "owned" by a single object.
* An object being dirty does not imply owner objects are dirty.
* Ergo, an object only has to flush its own direct state, not recursively
  any state of sub-objects.
* In the previous schedule/commentary example, the schedule node doesn't
  write any commentary files; the commentary object itself has to be
  dirty for this to happen.
* Similarly, a new child schedule added to an idea node doesn't dirty
  the idea node, because there's no physical idea node state to modify.
  The relationship is encoded solely through the new schedule node's `parent.txt`.

Whenever you list the contents of a directory, this should become
a `dict` mapping some unique key (often derived from filename)
to initially-empty objects.
So you don't have to list the contents of any directory twice.
Those empty objects become non-empty if actually explored.

On startup,
* Parse the current idea state, but don't raise any exceptions for parser errors.
  These get encoded into `CurrentIdeaState` and raised only if needed.
* Initialize the node dicts by listing files in the `sch/` and `idea/` directories.
  Each is stored as an empty-state `ScheduleNode` or `IdeaNode`.

Some objects' state is encoded by the presence or absence of a file
(weird design motivated by my anti-git-merge-conflict goal).
For example, the canonical schedule of an idea node is like this.
Don't try to read a nonexistent file more than once.
So in this example, the `CanonicalSchedule` object has to encode a
tri-state (a) empty (unknown state), (b) doesn't exist, (c) exists.


### Reference Build Commands

The following was tested end-to-end against the local Halide build at
`~/Halide/build/` and produces a standalone binary that both benchmarks
(via the profiler) and emits the `.stmt` and `conceptual.stmt`. In these examples the
name `brighten` is used for both `-g` (generator name) and `-f` (output
basename) since the example generator registers `brighten`. The real tool
instead **discovers** the `-g` name (run the generator exe with no `-g` and
scrape the `available Generators are:` list, which holds a single name under
the single-generator assumption) and passes a **fixed** `-f` basename such as
`dh_hl_gen`; this decoupled variant was also tested end-to-end.

**Gotchas that cost time (all confirmed on David's MacBook):**

* `HalideBuffer.h` and `HalideRuntime.h` are **not** in `build/include/`
  (which only has `Halide.h`); they live in `~/Halide/src/runtime/`,
  so `RunGenMain` must be compiled with `-I ~/Halide/src/runtime`.
* The `conceptual_stmt` emit produces a file with extension **`.conceptual.stmt`**,
  not `.conceptual_stmt`. (The plain `stmt` emit produces `.stmt`.)
* Compile `RunGenMain` with `-fno-exceptions -DHALIDE_NO_PNG -DHALIDE_NO_JPEG`
  so it doesn't drag in libpng/libjpeg; benchmarking uses random/estimated
  inputs, so no image I/O is needed.
* The `static_library` emit already embeds the Halide runtime, so no separate
  `runtime.a` needs linking (unlike Halide's own root `Makefile`, which emits
  with `no_runtime`). Only `-lpthread -ldl` are needed at link time.

Phase 1 -- build the generator executable (the `GenGen` main lives inside
`libHalide_GenGen.a`):

    c++ -std=c++17 -O2 -I$H/include -I$H/../tools \
        generator.cpp -o generator_exe \
        $H/tools/libHalide_GenGen.a -L$H/src -lHalide -Wl,-rpath,$H/src

Phase 2 -- run the generator; append generator params as trailing `key=value`
tokens (formatted per the `%d`/`%r` rule above):

    ./generator_exe -g brighten -o . -f brighten [key=value ...] \
        -e static_library,c_header,registration,stmt,conceptual_stmt \
        target=host-profile

Phase 3 -- compile `RunGenMain` (note the `src/runtime` include):

    c++ -c -std=c++17 -O2 -fno-exceptions -DHALIDE_NO_PNG -DHALIDE_NO_JPEG \
        -I$H/include -I$H/../src/runtime -I$H/../tools -I. \
        $H/../tools/RunGenMain.cpp -o RunGenMain.o

Phase 4 -- link the standalone binary:

    c++ -std=c++17 -O2 RunGenMain.o brighten.registration.cpp brighten.a \
        -o brighten.rungen -lpthread -ldl

Run / benchmark:

    HL_PROFILER_JSON_OUTPUT=out.json ./brighten.rungen --benchmarks=all --estimate_all --verbose

where `$H` = `~/Halide/build`. For `profile`, phase 1 runs once and phases
2--4 + the run loop over each parameter set. Only phase 2 sees the generator
params, so the loop must re-emit and re-link per parameter set.

A worked, tested generator + ninja build is under
`dendritic_hl/rungen_example/`. Run it with

    ninja -f build_ninja.txt brighten.rungen

The ninja file is named `build_ninja.txt` (not `build.ninja`) so it escapes
this repo's `*.ninja*` gitignore rule and can be committed; hence the `-f`.


### History Tool -- Implementation Details

    dh_hl history {workspace file name} [schedule ID]

Walk the branch of the tree starting from the referenced schedule node,
going up towards a root node.

Starting at the referenced schedule node, print:

* Its ID
* Its child idea nodes in the same format as `dh_hl list_ideas`.
  Mark the child idea node that is the parent of the previously printed schedule node.
  Try to recycle common code plz.
* For each commentary file, print its timestamp on one line,
  and print the first up-to 72 characters of the first line of the commentary text.

After each printed schedule node, move on to its parent idea node's parent schedule node,
and stop after printing the root schedule node reached.

Add some minimal formatting to make it look nice.
Put conspicuous dividers between the info printed for each schedule node.

When traversing up the tree,
check that the two edges follow the tree structure timestamp invariant.
So we are guaranteed not to end up in an infinite loop
even if the catalog state is cooked.

FUTURE: use the `importance` stuff to filter to less info.


## Tests

There is a `tests/` directory holding a `pytest` suite for the harness.

**Test-only dependencies.** The `dh_hl` package itself is Python-3-standard-library
ONLY (see the Dependency scope goal in [idea.md](idea.md)). The **tests**, which
are never shipped or imported by the package, are allowed two extra packages:

* `pytest` -- the test runner (fixtures, `tmp_path`, `monkeypatch`, parametrize).
* `hypothesis` -- property-based testing, used for the ID round-trip properties
  in `test_ids.py`.

Install these *only* into a throwaway environment; do NOT add them to any
runtime path. On David's MacBook they live in a git-ignored virtualenv:

    python3 -m venv dendritic_hl/.venv
    dendritic_hl/.venv/bin/python -m pip install pytest hypothesis

(A venv rather than a global `pip install` because the system Python here is
Homebrew's, which refuses global installs under PEP 668. Any environment with
the two packages works.)

**Running.** From the `dendritic_hl/` directory:

    .venv/bin/python -m pytest -m "not halide"   # fast; no Halide needed
    .venv/bin/python -m pytest                    # full suite

Most tests are Halide-free (they exercise the catalog model, tools, short IDs,
safety/rollback, and build/profile logic with the subprocess steps stubbed).
The genuinely end-to-end tests are marked `halide` (registered in `pytest.ini`)
and auto-skip unless the local `~/Halide` build and `ninja` are present.

**Test-only hook in shipped code.** `safety.new_file` honors a
`DH_HL_TEST_FAIL_AFTER=<n>` environment variable that raises after the n-th new
file created in a run. It is a no-op unless that variable is set, and exists
solely so a subprocess test can prove the `atexit` rollback restores a partial
mutation end-to-end (the real rollback path only fires at true interpreter
exit). It is the one concession to testability in otherwise test-agnostic code.

**Monkeypatch seams (build/profile).** `tests/test_build_fake.py` exercises the
`build`/`profile` orchestration without a real Halide toolchain by using
`pytest`'s `monkeypatch` to replace, *by name*, the `build.py` helpers that
shell out: `_write_ninja`, `_ninja_build`, `_discover_generator_name`, `_emit`,
`_link`, `_run_benchmark`. A test also calls `_emit` directly and inspects the
argv it builds. Consequences to keep in mind:

* These helpers' **names and signatures are a lightly load-bearing test
  contract.** Renaming, inlining, or re-signaturing one breaks the fixture
  (`monkeypatch.setattr` raises on a missing attribute); update it in the same
  change. `pytest` points straight at the break, and the fix is mechanical.
* Because the fixture *replaces* these functions, the fake tests never run
  their real bodies, so edits *inside* a body (e.g. compiler flags in `_link`,
  ninja rules in `_write_ninja`) are invisible to them -- those bodies are
  covered only by the opt-in `halide`-marked `test_halide.py`.

So the two tiers are complementary: fake-build pins the orchestration fast and
always; the Halide test verifies the real toolchain integration when present.
