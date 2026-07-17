# Implementation Notes for Dendritic Halide Harness (dh_hl)

CLAUDE: these "CLAUDE" notes ask for edits to the file.
You don't have to do this immediately.
This can be done before or after implementing the concurrency+session changes,
whichever seems most natural.


# Catalog Directory State

The top-level catalog directory contains sub-directories for each node type:

* `idea`
* `sch`
* `session`

as well as

* `private` directory
* `.gitignore`, ignores `private`


## Schedule Nodes on Disk

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
No automatic fix provided: this power should be used very sparingly anyway.

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


## Idea Nodes on Disk

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


## Session Nodes on Disk

Each session node is stored in a `session/{id}` subdirectory of the catalog directory.
The gitignored session private workspace is stored separately to ensure git checkouts
can cleanly create and destroy this directory.
The state is:

CLAUDE: implement whatever username/hostname sanitization you think is reasonable
and document briefly here.

* **ID:** directory name.

* **Parent:** `parent.txt` holds a session node full ID plus a newline,
  unless there is no parent, in which case this file doesn't exist.

* **Seed Idea:** `seed_idea.txt` holds an idea node full ID plus a newline.

* **Output Schedule:** `output_schedule.txt` holds a schedule node full ID plus a newline,
  unless there is no output schedule, in which case this file doesn't exist.

* **Delisted Flag:** Delisted iff `delisted.txt` exists; contents are ignored.

* **Depth:** implied from the ID; parse all digits before the first `_`.
  Note, the depth will always be formatted as-if by `%d`
  (base 10, no redundant leading 0s etc.)

* **Timestamp:** implied from the ID

*Merge risk:* `output_schedule.txt`, no automatic fix provided.


### Session Private Workspace

Inside the `private/{session id}` sub-directory, there is

* `session.lock`, lock file (contents ignored)

* `generator.cpp`, workspace C++ file

* `current_idea_state.txt`, current idea state

* `bin/` directory

Any command accessing `private/` or giving paths to it (`dh_hl bin` etc.)
must initialize `private/{session id}` and its contents lazily,
except for `generator.cpp` and `current_idea_state.txt` (we can't know this).
This could desync from `session/` due to git checkouts.


### Current Idea State on Disk

Stored in session private workspace as `current_idea_state.txt`.
It's a single line with trailing newline holding

* `dendritic_hl_root({timestamp})` to encode the "no current idea" state

* `dendritic_hl_idea({idea node full ID})` to encode the "some current idea" state

FUTURE: there's a bunch of "merge conflict" handling that's not really useful at the moment.
If it's relatively harmless, I'd like to keep it for now,
in case circumstances cause me to again change my mind about gitignoring `current_idea_state.txt`.


## Session Handles on Disk

Each session handle is `tmp.` followed by a series of lowercase hex digits.
The mappings from session handle to (catalog, session) are stored in the
`handles/` sub-directory of the machine directory
(e.g. `~/.cache/dendritic_hl/handles/tmp.3f9a`), one immutable file per handle,
with the filename being the handle. (This is the machine-local alias store; it
is distinct from the catalog's git-tracked session-node storage.)

The scheme is **lock-free** in both directions. The key property that makes it
safe under concurrency is that a handle file only ever becomes visible under
its final name *already containing complete content* -- never empty or
half-written. That is achieved by writing a fully-formed temp file first and
then atomically hard-linking it into place (create-or-fail); we never write
content directly to the final name.

Pseudocode:

    # \n still works if catalog_dir_abspath somehow contains a \n,
    # and preserves readability compared to \0
    encoded_pair = bytes(catalog_dir_abspath + "\n" + session_full_id + "\n", "utf-8")
    H = sha256(encoded_pair).hexdigest()

    # Stage the complete content in a temp file that is a SIBLING of the final
    # handles (same directory => same filesystem, so os.link never hits EXDEV).
    tmp = handles/.alloc.<pid>.<rand>
    write(tmp, encoded_pair); close(tmp)     # fully written before it is ever linked

    # Find the shortest hash prefix not already assigned to a different pair.
    for k in 1,2,3,...:
        cand = "tmp." + H[:k]
        try:
            os.link(tmp, handles/<cand>)     # atomic create-or-fail; content already complete
            result = cand; break             # done, this is our handle
        except FileExistsError:
            if read(handles/<cand>) == encoded_pair:   # safe: <cand> is always complete
                result = cand; break         # someone already allocated it for us; reuse
            else:
                continue                     # collision with a different pair -> lengthen
    os.unlink(tmp)                           # (harmless to leak on crash; it's re-derivable cache)
    return result
    # The loop cannot run out of prefixes without a full-length SHA256 collision.

**Translating from** a session handle requires no locking: read
`handles/<handle>` and error out if it is missing or unparsable. This is safe
precisely because of the link idiom above -- any name a reader can see points
at complete bytes.

**Allocating** a session handle (or finding the existing one) also requires no
locking. The `os.link` create-or-fail is atomic and self-arbitrating: racing
allocators for the same pair converge on the same handle (loser reads equal
bytes and reuses), and racing allocators for different pairs that collide on a
prefix simply lengthen. Note the concurrent machine lock would *not* help here
even if held -- it is shared, so it does not serialize allocators -- and the
exclusive one is reserved for profiling; the link idiom is what provides
correctness, not a lock.

Recommendation: be extremely tolerant of junk on disk. Never parse handle-file
contents; encode the `(catalog, session)` pair to `bytes` once and do a raw
bytes comparison against what is on disk, treating any unreadable/short/garbage
file as "not a match" (so a crashed half-written `.alloc.*` temp, or any stray
file, is simply skipped rather than trusted).

FUTURE: an alternative design for idea/schedule node short IDs
might also accept this "machine local, but never invalidated" tradeoff.
For now, Claude advises sticking with the existing short ID scheme
for ideas and schedules, and not risk perturbing the current code.
There is a very small risk that an agent may receieve a short ID,
try to use it later, and find it's now ambiguous
due to another concurrent agent's action.
For now, the mitigation is the 6-hash-char minimum rule,
and always giving an explicit ambiguity error instead of guessing.


## Efficiency

This is not a super elegant format, which is kind of abusing the file system.
For a production implementation, I should probably stop creating thousands of files.
Furthermore the `sch/` and `idea/` directories will end up becoming large,
requiring `O(n)` time for most tools.

It's mainly my goal of avoiding difficult git merge conflicts that yielded this design,
as creating separate files will not conflict, but editing a single "node list" file will.

A production implementation would probably require a more efficient graph format,
along with tools for automatically resolving merge conflicts.

This design is risky in light of Windows traditional `MAX_PATH=260` limit,
which Python 3 is compiled to work around.
But we are already not portable to Windows for other reasons anyway.
Mac limit is `1024` characters; should be plenty.


## Status Tool — Implementation Details

    dh_hl status

This is a purely read-only command.

If there was no current session given, the tool errors.
Otherwise, the tool tries to find a schedule node that already holds the workspace C++ file
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

* The full IDs of the current session and its parent session (if any)

* The is-delisted flag of the current session

* The IDs of the session's seed idea node and output schedule node
  (may be none for the latter)

* Give the current idea state
  (no current idea/some current idea/parse error/missing/etc.).
  Try to print errors cleanly if something is wrong with the state on disk.
  If the current idea node exists, print the status of its canonical schedule (none, or ID of it).
  If the current idea state is syntactically correct but references a nonexistent idea node,
  advise of that too (could happen due to a git checkout).

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


## Build/Profile Tools — Implementation Details

    dh_hl build [parameters file]
    dh_hl profile [parameters file]

The steps are:

(1) Compile the Halide generator in the session private workspace `bin` directory
(1b) (`build` only) further generate the Halide binary and `stmt` files
(2) Find or create the edited schedule node
(3a) (`profile` only) profiling loop, with machine exclusive lock
(3) Update edited schedule node
(4) Print the ID of the edited schedule node

**(1)** These steps require only the session private workspace.
They are run with only the catalog lock and concurrent machine lock held.
(See "Tool Safety — Lock Hierarchy")

For step (1b), print the file names (in the `bin/` directory)
of the emitted `.stmt` and `conceptual.stmt` files.
They can be overwritten by future builds.
Pipe all compiler and generator output `stdout` and `stderr`
to the harness's `stdout` and `stderr`.

**(2)** At this point, the catalog lock must now be acquired.
Furthermore, for profiling only, the machine lock must be upgraded
to an exclusive lock (prior to catalog lock, per "Tool Safety — Lock Hierarchy").
These late acquisitions ensure the expensive C++ compilation step
doesn't needlessly block other agents from using `dh_hl`.

The edited schedule node is:

* If `dh_hl status` would give an unambiguous schedule node,
  that schedule node is the one this tool edits.
* Otherwise, if there is no current idea node,
  give an error, and suggest the `set_idea` and `new_root` tools.
* Otherwise, add a new child schedule node to the current idea node
  holding a copy of the workspace file.

**(3)** The per-generator-paramaters-object generate/profile/results loop
runs with the machine lock held exclusively, as mentioned.
It is somewhat wasteful that the machine lock is still held exclusively
during the Halide generator run, but a generator is fast enough and
I don't need the complication.

FUTURE: if it's really an issue, we can fission the loop into
"compile all binaries" and "profile all binaries" loops.

**(4)** Finally (after all other printing including the sub-processes),
print the ID of the edited schedule node.

Don't relinquish the locks: this last step is fast enough.


### Build/Profile Decisions

**Build driver split (decided):** use `ninja` only for the param-independent
steps, and drive everything param-dependent from Python `subprocess`:
* Ninja builds phase 1 (the C++ workspace file -> Halide generator executable)
  and compiles `RunGenMain.o`. These are built ONCE and don't depend on
  generator parameters.
* Python drives the param-dependent phases directly with `subprocess`
  (serially, no parallelism): run the generator to emit outputs (phase 2),
  link the standalone binary (phase 4), and, for `profile`, run the benchmark.
  For `profile` this per-param-set work is a Python `for` loop; don't push the
  loop into ninja. This keeps David from getting paranoid about unexpected
  parallelism (yes, I know about pools).

The steps performed are:
* compile the C++ workspace file to a Halide generator executable (ninja)
* run the generator to emit the AOT static library, header, `registration.cpp`,
  and both the plain `.stmt` and `conceptual.stmt` files, using
  `target=host-profile` (Python)
* link `RunGenMain` against the generated `registration.cpp` + static library
  to finish a standalone benchmarkable binary (Python)

See the [Reference Build Commands](reference_build_commands.md) file for the
tested build/link recipe. For `profile`, keep the generator executable from
phase 1 and re-run the emit + link + benchmark steps for each parameter set.

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

**Generator parameters.** when executing the generator,
each numeric parameter value must be formatted with `%d` if it's a whole number,
and with `%r` if not, to ensure no unexpected decimal points and no floating point roundoff.



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

We require `flock` file locking to make concurrent harness usage safe.

Tools can assume sha256 collisions never happen.

Tools NEVER overwrite or modify existing files, except for:

* tmp files
* private session workspace files
* `result.txt`
* `canonical.txt` for `fix_canonical` tool
* `parent.txt` for `fix_canonical` tool (see note below)

Accordingly, use `"x"` mode or equivalent when creating new files.

**OVERRIDING SAFETY RULE:** never use "recursive directory delete" functions.
If you delete files and directories in the opposite order as creation,
a directory will be empty when deleted, so `os.rmdir` will work.
This rule prevents PERMANENT DAMAGE from bugs (like deleting my `home`).


### `parent.txt` overwrite exemption for `fix_canonical`

`fix_canonical` re-parents the *newer* competing canonical schedule so it becomes
the canonical schedule (hence a child) of a freshly created resolution idea
node. Because a schedule's parent edge is stored solely in its `parent.txt`,
and the newer schedule already has a `parent.txt` pointing at the original
idea, honoring that description requires **overwriting** the newer schedule's
`parent.txt`. This is the sole tool permitted to overwrite `parent.txt`, and
like the other overwrites it is deferred and not rolled back. (This is a
deliberate exception to the otherwise strict anti-`parent.txt`-overwrite
stance; the whole file layout exists to avoid `parent.txt` churn, but this
rare recovery tool is the documented escape hatch.)


### Tool Safety: File Rollback

Make all changes to the catalog atomic as much as possible.
For any tool run, record a list of new files and new directories created
(not existing directories touched).
Use a common helper for this.

If the tool fails, an `atexit` handler will run that deletes all those recorded new files and directories
(disable the `atexit` handler as the final step before successful exit,
so we don't delete new files when the tool succeeds!)
Again, be very very careful about new vs. existing directories.
Don't delete any directories you didn't create.

So flushing stays a single step strictly separated from the main logic (per
the Tool Internal Design); "did a subprocess fail" affects only the process
**exit code**, never whether we flush.

The "new files" don't include the workspace C++ file and the special case overwritten files.
NEVER delete the workspace C++ file.
Except for tmp files and private session workspace files,
defer overwriting files as the FINAL step of tools, because we don't roll back these overwrites.
So overwriting as late as possible minimizes the risk of crashing after the overwrite.

Make SIGQUIT raise `KeyboardInterrupt` and try to prevent `KeyboardInterrupt` and exceptions from stopping the `atexit` handler.
(But don't get stuck in an infinite loop if an `OSError` happens).
Caveat: we can't stop SIGKILL or other hard crashers; atomicity fails in these cases.

CLAUDE: upgrade this section to reference the real safety system and helpers implemented

*What counts as "the tool fails" (rollback) vs. a catalogued bad outcome:*
The rollback is for **harness/logic failures** — an unexpected exception, a
pre-flight validation error, an environment problem — i.e. cases where the
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


### Tool Safety: Lock Hierarchy

The locks are:

* Machine Lock (`~/.cache/dendritic_hl/machine.lock`):
  meant to be global to the machine.
  Acquired exclusively for profiling, concurrently for all other uses.

* Session Lock (`{catalog}/private/{id}/session.lock`):
  exclusive lock per session node on disk; protects only the private session workspace.
  Unlike the other locks, this lock isn't necessary with correct tool usage and just exists
  to give a prominent warning of "concurrent session use detected".
  The agent is allowed to work in the private session workspace without this lock.

* Catalog Lock (`{catalog}/private/catalog.lock`):
  acquire exclusive access to the catalog directory,
  other than private session workspaces.

Each tool may do a subset of these four actions, done strictly in top-to-bottom order:

* Acquire concurrent machine lock (blocking).
  Mandatory for all tools, even "cheap" ones.
  Acquire before any other Python logic to
  prevent as much profiler CPU competition as possible.

* Acquire exclusive session lock; non-blocking and exit-with-failure if not acquired.
  Acquire this only for tools that need the private workspace or *mutate* session nodes
  (so read-only queries of the session's *git-tracked catalog state* won't fail).
  The failure message is:

    AGENTS: stop work immediately. Concurrent usage of session detected.
    This could be due to an agent error (e.g. same session given to 2 agents)
    or human user action interfering with agent work.

* "Upgrade" machine lock to exclusive, by releasing it then re-acquiring exclusively

* Acquire exclusive catalog lock (blocking)

For now, the assumption is still that tool calls are short-lived, so we just rely on the OS
to release the locks upon process exit, which happens strictly after the `atexit` safety handlers.
Ergo, atomicity rollbacks will occur with the catalog lock still held.
Unlocking works even if the process is killed or segfaults, unlike the `atexit` file deleter.

Possible objection, lock inversion:
The release-and-acquire upgrade of the machine lock isn't a deadlock
risk with the session lock, because the session lock is non-blocking.

Possible objection, starvation:
We currently don't give exclusive machine locks any priority,
so profiling runs can stall for a long time.
If we assume all agents will eventually kick off profiling in finite time,
eventually all agents will be waiting for the exclusive lock and one will proceed.

I want locking to work "by design" in an overarching abstraction,
and not require most code to worry about whether the lock was held.
Maybe,

* The existing safety system is the bottom-level arbiter for keeping the rest of the code behaving well.
  It exposes "lock" functions, and asserts no locks acquired out-of-order.

* `flush` methods for session node state assert that the session lock was held?

* Machine lock acquired first thing in `main()`
  or even before that if possible (minimze imports before lock).

* `Catalog` either auto-locks or checks the catalog lock state from the safety system.
  Can assume the catalog lock is held if you already have this object or a catalog sub-object.

CLAUDE: update this section with real decisions made and reference to implementation functions.


### Tool Safety: Tree Structure Invariants

Remember to check tree structure invariants whenever adding new edges.
You are responsible for ensuring the tree structure invariants are
not violated even if it's not explicitly spelled out as a failure mode of the tool.
Hence it's strongly advised to use a common helper function for checking and adding edges.

CLAUDE: was this common helper implemented, and if so, give its name here.


### Tool Safety: Timestamp Conflicts

CLAUDE: explain fresh timestamps and your minting scheme.


## Tool Internal Design

CLAUDE: upgrade this section to reference the real functions/variables implemented

CLAUDE: give top-down sketch of the codebase

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
* Initialize the node dicts by listing files in the `sch/`, `idea/`, `session/` directories.
  Each is stored as an empty-state `ScheduleNode`, `IdeaNode`, or `SessionNode`.

Some objects' state is encoded by the presence or absence of a file
(weird design motivated by my anti-git-merge-conflict goal).
For example, the canonical schedule of an idea node is like this.
Don't try to read a nonexistent file more than once.
So in this example, the `CanonicalSchedule` object has to encode a
tri-state (a) empty (unknown state), (b) doesn't exist, (c) exists.


### History Tool — Implementation Details

    dh_hl history [schedule ID]

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

* `pytest` — the test runner (fixtures, `tmp_path`, `monkeypatch`, parametrize).
* `hypothesis` — property-based testing, used for the ID round-trip properties
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
  ninja rules in `_write_ninja`) are invisible to them — those bodies are
  covered only by the opt-in `halide`-marked `test_halide.py`.

So the two tiers are complementary: fake-build pins the orchestration fast and
always; the Halide test verifies the real toolchain integration when present.

CLAUDE: document plan for listening in on locking behavior,
or defer as future work if it's too difficult.
