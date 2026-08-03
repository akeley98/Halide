# Implementation Notes for Dendritic Halide Harness (dh_hl)

These are the implementation-side companion notes to [idea.md](idea.md) (the
behavior contract).  Keep the two in sync when changing a tool.

For now this tool is an early prototype and backwards compatibility is a non-goal.
So do not worry when implementing changes that would break reading old catalogs.

IMPL TASK: paragraphs like these highlight where the doc describes features
not yet implemented in the actual code (that's the agent's job).
When you're reasonably confident the task is done, delete the IMPL TASK paragraph.
Leave them in if there's significant doubt,
or clarification is required from the user.

NOTE: I got tired of the split of per-tool information between
`idea.md` and `impl.md`, so now the tool-specific implementation information
is in `idea.md` only.
At some time I'll figure out how to strip this from the prompt to avoid
wasting tokens for agents using the harness (not implementing it).


# Stable Hostname

Linux: Read `/etc/hostname`

Mac: `scutil --get ComputerName`; seems to be the only stable option on Mac
so I'll just tolerate that `hostname` is a misnomer here.
(I'd rather not expand to `hostname_on_Linux_ComputerName_on_Mac`).

**Implemented** as `ids.stable_hostname()`: `scutil --get ComputerName` on
`darwin`, `/etc/hostname` otherwise, falling back to `socket.gethostname()` if
the platform path fails (so profiling never crashes on a name lookup).  The RAW
string is returned; every use as an ID or filename runs it through
`ids.sanitize_component` first (session IDs via `make_session_id`, the benchmark
`bench/{hostname}_{ts}.json` file name in `build.py`).  Only the benchmark JSON
`hostname` field keeps the raw value (idea.md "Benchmark Sub-object State").

The Mac path is tested; the Linux path is best-effort and David will verify it
on the mantissa machine.  A machine-specific reminder test
(`tests/test_ids.py::test_stable_hostname_hardwired_for_known_machines`)
hard-wires that `username == "dakeley"` implies `David’s MacBook Pro` and
`username == "mantissa"` implies `MantissaAmpere` (a no-op on any other
machine).  NB the real macOS `ComputerName` uses a typographic apostrophe
(U+2019), not the ASCII `'` this note was casually written with.

**Benchmark JSON** `hostname`:
Left **raw** (unsanitized) — a deliberate hedge against losing information, since a
Mac's `ComputerName` may contain spaces/punctuation (e.g. "David's MacBook
Pro").  Everywhere the hostname is used as an ID or filename (session IDs, the
`bench/{hostname}_{ts}.json` file name) it is first run through
`ids.sanitize_component`.


# Catalog Directory State

The top-level catalog directory contains sub-directories for each node type:

* `idea`
* `sch`
* `session`
* `benchmark_sets`

as well as

* `private` directory
* `.gitignore`, ignores `private`


## Schedule Nodes on Disk

Each schedule node is stored in a `sch/{id}` subdirectory of the catalog directory.
This contains files and directories holding state:

* **C++ source code:** `generator.cpp`

* **Generator Parameters:** `generator_parameters.json`

* **Hash:** computed from the UTF-8 encoding of `generator.cpp` and
  `generator_parameters.json`, concatenated.
  Forms part of the full ID.
  Implemented as `ids.schedule_content_hash`, shared by `Catalog.create_schedule`
  and `SessionWorkspace.workspace_hash` so node and workspace never disagree.

* **UTC wall time timestamp:** forms part of the full ID.

* **Edges:** `parent.txt` holds the full ID of the parent idea node plus a newline,
  unless this schedule node is a root node, in which case `parent.txt` doesn't exist.
  Edges to child idea nodes are *derived state*.
  Scan the `idea` nodes directory (to be defined)
  for nodes whose full IDs have the correct `parent id`.

  *Alternate design* had multiple parent ideas possible (DAG not tree),
  which was helping the "git compatibility" goal (e.g. encode merge conflict resolution),
  but just raised too many tough cases for a prototype with questionable payoff.

* **Result:** `result.txt`,
  holding `unknown`, `c++ error`, `halide error`, `runtime error`, or `success`.
  The default value is `unknown`.
  Ranked worst-to-best by `catalog.RESULT_RANK`; `build` only ever moves a node
  to a better value (`catalog.best_result`).

* **Benchmark Sub-objects:** store in `bench/{hostname}_{timestamp of benchmark}.json`
  (the `{hostname}` here is the *sanitized* stable hostname — see "Stable Hostname").

  The `{hostname}_{timestamp of benchmark}` part is the benchmark's *local ID*;
  it is exactly what's after the last `.` in a benchmark short ID (and exactly the
  file-name stem), so don't parse files to resolve IDs.  The benchmark's *full ID*
  prepends the parent schedule full ID
  (`{parent schedule full ID}_{hostname}_{timestamp}`).  Because the timestamp is
  fixed width and the schedule prefix is fixed width, the hostname in the middle
  parses out unambiguously even though it may itself contain `_`.  Implemented as
  the `Benchmark` class in `catalog.py`; resolution/formatting are the
  `_resolve_benchmark` / `_format_benchmark_short` free functions (exposed via
  `Catalog.resolve_benchmark` / `Catalog.format_benchmark_id`).  The benchmark
  JSON gains a `warnings` list (see idea.md "Benchmark Sub-object State" and the
  `HL_PROFILER_JSON_TEMPORARY_WARNINGS` note in `reference_build_commands.md`).

* **WarningToggle Files:** store in `warning_toggle/{timestamp}.json` with
  key-value pairs `citation` (a full commentary ID, from anywhere in the catalog),
  `rule`/`func` (the blocked warning's rule slug + func name, or both null), and
  `cancels` (the full ID of another `WarningToggle` this one cancels, or null).
  The on-disk value is a tagged union: either `cancels` is set (a cancel) or
  `rule`/`func` are set (a block), never both.  Unlike commentary `cancels`, a
  `WarningToggle`'s `cancels` stores a *full* ID, because these may cross schedule
  nodes.  The sub-object's *local ID* is its `{timestamp}`; its *full ID* prepends
  the parent schedule full ID (`{parent schedule full ID}_{timestamp}`).
  Implemented as the `WarningToggle` class in `catalog.py`; resolution/formatting
  are `_resolve_warning_toggle` / `_format_warning_toggle_short` (exposed via
  `Catalog.resolve_warning_toggle` / `Catalog.format_warning_toggle_id`).  The
  block algorithm (idea.md "WarningToggle State") lives in
  `Catalog.warning_toggle_state` / `blocking_toggle`, walking
  `Catalog.schedule_path_to_root`.

  *Merge risk:* (unlikely) incoming different WarningToggles with the same
  timestamp on the same schedule node.  No automatic fix provided.

* **Commentary Files:**
  store in `comment/{timestamp}_{hash}.json` (where `hash` is the sha256 of the
  commentary text) with key-value pairs:
  * `text`: text of commentary
  * `review`: review value (one of `neutral`/`negative`/`positive`/`lost_interest`)
  * `cancels`: list of strings; each giving the `{timestamp}_{hash}` value
    of a commentary sub-object in the cancels list.
    NB this makes inter-schedule-node cancellations literally impossible to express.

  The commentary's *local ID* `{timestamp}_{hash}` happens to have the exact
  shape of a schedule full ID, so the loader/resolver reuse the `ids`
  schedule-ID helpers on it.  Its *full ID* prepends the parent schedule full ID
  (`{parent schedule full ID}_{timestamp}_{hash}`).  Implemented as the
  `Commentary` class in `catalog.py`; resolution/formatting are the
  `_resolve_commentary` / `_format_commentary_short` free functions (exposed via
  `Catalog.resolve_commentary` / `Catalog.format_commentary_id`).

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

* **Idea Side Links:** Idea side links are encoded by the mere existence of files.
  This design is to prevent merge conflicts.
  The files are empty.
  `idea/{id A}/borrows_from/{id B}` encodes a "borrows" link from A to B.
  `idea/{id A}/superseded_by/{id B}` encodes a "superseded-by" link from A to B.
  Implemented as `IdeaNode.side_links` / `IdeaNode.add_side_link` (the empty
  presence files are created in `IdeaNode.flush`); the `add_idea_side_link` tool
  is a silent no-op on an exact duplicate.


## Session Nodes on Disk

Each session node is stored in a `session/{id}` subdirectory of the catalog directory.
The gitignored session private workspace is stored separately to ensure git checkouts
can cleanly create and destroy this directory.
The state is:

**Username/hostname sanitization (implemented, `ids.sanitize_component`):** each
of `username` (from `getpass.getuser()`, falling back to `"user"`) and `hostname`
(from `socket.gethostname()`) has every character outside `[A-Za-z0-9_-]` mapped
to `_`, is truncated to 64 chars, and is never empty (an all-stripped value
becomes `"_"`). De-anonymizing is intentional, so there is no hashing. The `@`
between them is therefore the unique separator, and since the timestamp is fixed
width, `is_session_id`/`session_depth`/`session_timestamp` parse the ID
unambiguously (`_SESSION_ID_RE` in `ids.py`).

* **ID:** directory name.

IMPL TASK: prompt

* **Prompt:** `prompt.txt`

* **Parent:** `parent.txt` holds a session node full ID plus a newline,
  unless there is no parent, in which case this file doesn't exist.

IMPL TASK: new seed ideas, multiple and not just one.

* **Seed Idea:** `seed_ideas.json` holds a list of idea node full IDs.

IMPL TASK: default anchor schedule

* **Default Anchor Schedule:** if it exists, its full ID plus a newline is in
  `default_anchor_schedule.txt`

IMPL TASK: implement your own reasonable outputs JSON internal format
and document in a `###` sub-section right under this.

* **Outputs:** `outputs.json`, doesn't exist if no outputs yet.

* **Delisted Flag:** Delisted iff `delisted.txt` exists; contents are ignored.

* **Depth:** implied from the ID; parse all digits before the first `_`.
  Note, the depth will always be formatted as-if by `%d`
  (base 10, no redundant leading 0s etc.)

* **Timestamp:** implied from the ID

*Merge risk:* `outputs.json`, no automatic fix provided.


### Benchmark Sets on Disk

Stored in `benchmark_sets/{full id}.json` in the same format as would
be exposed to the end user by the `json_benchmark_set_info` tool.
Implemented as the `BenchmarkSet` class in `catalog.py`, created by
`Catalog.create_benchmark_set` (mints the `{host}_{ts}` full ID) and resolved
by exact full-ID match (`Catalog.resolve_benchmark_set`; no short IDs).

*Merge risk:* low: will never conflict for benchmark sets generated
on different machines with different sanitized hostnames, and will not
conflict on one machine as long as the timestamp minting is not
circumvented (e.g. by intentionally copying the catalog to two
different directories and brute forcing a timestamp collision)


### Session Private Workspace

Inside the `private/{session id}` sub-directory, there is

* `session.lock`, lock file (contents ignored)

* `generator.cpp`, workspace C++ file

* `generator_parameters.json`, workspace generator parameters file

* `current_idea_state.txt`, current idea state

* `bin/` directory

* `current_anchor_schedule.txt`, full ID of schedule node and newline.
  No file if there's no current anchor schedule.

IMPL TASK: replace .txt with .json and remove all documentation references
to the ordering of the stored ideas list.  The changes this turn are
to prepare for a future tool that shows current ideas ranked by pool
and cost. The cost is not to be implemented yet.

* `private_ideas.json`, the session private idea list (actually JSON object).
  The keys are the set of idea node full IDs comprising the list.
  The values are the pool tags (strings).
  The cost is not stored here; it's derived when needed.

IMPL TASK: private benchmark set list.  The values will become
non-trivial once cost is implemented.  Please implement a separate
object with an allow-overwrite flush to store the private benchmark
set list.  In the future, the value will be initialized when a new
benchmark set is added to the list, so a centralized "add benchmark
set" helper will be prudent.
Discuss with me if the "new flushable object" plan is questionable.

* `private_benchmark_sets.json`, the session private benchmark set list.
  The keys are the set of benchmark set full IDs comprising the list.
  The values are currently empty objects `{}`.

* `init_build.json`, left behind by `dh_hl init_build`: the catalog-relative
  `generator.cpp` + `generator_parameters.json` paths of each schedule node to
  build (target/other/anchor).  This lets `build` compile without first
  acquiring the catalog lock -- `init_build` is a hack to make that locking
  easier.  Its format is documented in `build.py` (`_INIT_BUILD_FILE`), not
  here, as it's not of general interest.

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


### Private Ideas List State on Disk

one idea node full ID per line, newline-terminated, in the order the ideas were added,

with **new ideas appended to the END (bottom)** of the file. The file is absent
until the first idea is added (an empty list reads as no file). It is not robust to
malformed data: blank lines are skipped, and any line that is not the full ID
of an existing idea node is silently ignored by the listing tools (this can
happen after a git checkout desyncs the gitignored workspace from the catalog
graph). Because new IDs go on the bottom, `dh_hl list_private_ideas` prints
the list **backwards** (reads the file, reverses it) so the most recently
added idea is shown first. `SessionWorkspace.{read,add,remove}_private_idea`
(context.py) own this file; mutations go through `safety.write_allowed`
(whole-file rewrite), so they participate in the deferred-overwrite/rollback
machinery like the other workspace files.
Interacting tools: `new_idea` and the session-creation flow append (the
latter to the *parent/current* session's list, and `new_catalog` appends to
nothing since it has no parent session); `forget_private_idea` removes;
`list_private_ideas[_todo|_done]` read.


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


# Warning Delivery Hack — Assumptions (temporary)

Andrew Adams's profiler doesn't yet emit warnings in its main JSON, so today they
reach us through a side channel we fully expect to REPLACE (integrating warnings
into the profiler payload one day).  Every assumption about that hack is funneled
through `dendritic_hl_lib/profiler_warnings.py`; when a better mechanism arrives,
that module plus the call sites named below is the whole blast radius.  Inventory
of what currently bakes in the hack:

* **Env-var side channel:** `build.py::_run_benchmark` points the profiler at a
  scratch file via `HL_PROFILER_JSON_TEMPORARY_WARNINGS`.
* **Side-channel file shape:** `profiler_warnings.warnings_from_temp_file` reads
  that file as a *single* JSON object (nominally "JSON lines", but one generator
  per file) and lifts its inner `warnings` list.  Absent file => no warnings.
* **Storage shape:** `build.py::_build_benchmark_obj` stashes that list under a
  top-level `warnings` key of the benchmark JSON, SEPARATE from the `profiler`
  object.  `Benchmark.warnings` / `profiler_warnings.warnings_of_benchmark` read
  it back (and default to `[]` for pre-warnings benchmarks).
* **Warning-object fields:** each warning is a dict with `rule`, `func`,
  `message`, `canonical_id`; only `profiler_warnings.warning_{rule,func,message,
  key}` reach inside one.  `WarningToggle` blocks on the `(rule, func)` pair
  (`Catalog.blocking_toggle`); within-pipeline func-name collisions are ignored
  (idea.md, reference_build_commands.md).
* **Consumers:** `tools.py::cmd_view_benchmark_warnings` is the only reader today;
  future holistic "view benchmark" tools should go through `profiler_warnings`
  too, not re-parse the JSON.


# Build/Profile Decisions

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


# Tool Safety Requirements

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

We want all changes to the catalog to be atomic as much as possible.
Each tool run records a list of new files and new directories created
(not existing directories touched).
These can be deleted upon tool failure, preventing partial transactions.

However, there is a caveat: we cannot rollback *overwritten* files,
only new ones. We mitigate this risk by automatically delaying overwrites as
late as possible, just prior to tool exit. This reduces the surface area
of possible crashes in between overwriting a file and tool exit.

**As implemented** (`dendritic_hl_lib/safety.py`): the "common helper" is the
`safety` module, a process-global registry.

* `new_file(path, data)` creates a file with `O_CREAT|O_EXCL` and records it;
  `new_dir(path)` / `makedirs_tracked(path)` create directories, recording only
  the levels actually created. All recorded entries go on `_new_entries`
  (a LIFO list of `("file"|"dir", path)`).
* `write_allowed(path, data)` is for the allowed-to-change files: it `new_file`s
  when the target is absent (so rollback can remove it) and otherwise defers an
  overwrite via `queue_overwrite`. Deferred overwrites are applied by `commit()`
  and are NOT rolled back.
* `arm()` (called at the top of `main()`) registers the `atexit` handler
  `_rollback` and maps `SIGQUIT`→`KeyboardInterrupt`.
* `_rollback()` deletes `_new_entries` in reverse (files via `os.remove`, dirs
  via `os.rmdir`, so a created dir is empty when removed); it swallows `OSError`
  (drops that entry — no infinite loop) and retries the current entry on
  `KeyboardInterrupt`.
* `commit()` applies the deferred overwrites, then clears `_new_entries` so the
  still-registered `atexit` handler becomes a no-op — this is the "disable as
  the final step" that prevents rolling back a successful tool's effects.

The flush itself lives on the model objects, not `safety`: `Catalog.flush()`
calls every dirty object's `flush()` (see Tool Internal Design),
and `Context.finish()` is `catalog.flush()` then `safety.commit()`.
Test hook: `new_file` calls `_maybe_inject_failure()`,
which honors `DH_HL_TEST_FAIL_AFTER` (see Tests).

Lock ordering: the lock layer is `locks.py` (not `safety.py`), and its fds are
held open until process exit, so `_rollback` (an `atexit` handler) runs strictly
before the OS releases them. Because the catalog lock is acquired before any
catalog construction/mutation (the Catalog lock invariant, see Lock Hierarchy),
rollback runs with the catalog lock still held, as required — no `safety.py`
change is needed for that ordering.

*What counts as "the tool fails" (rollback) vs. a catalogued bad outcome:*
The rollback is for **harness/logic failures** — an unexpected exception, a
pre-flight validation error, an environment problem — i.e. cases where the
in-memory changes are incomplete or untrustworthy and must be undone. It is
**not** triggered by a subprocess reporting a bad *build outcome*. Recording a
schedule node whose C++ failed to compile (`c++ error`) or whose Halide
generator failed (`halide error`) is the build tool **succeeding at
its cataloguing job** (recall the goal: "all C++ source code ever compiled
will be catalogued"). Concretely, for `build`:

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
  Furthermore, the implicit translation of `[schedule ID]` args can run without the session lock.

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

    AGENTS: Concurrent usage of session detected.
    Don't run concurrent tool invocations.
    If the concurrent usage is not due to your error, stop and report the issue:
    this could be due to a parent agent error (e.g. same session given to 2 agents)
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

Possible objection, unlocked read-only session node access:
This is safe because the state is protected by the catalog lock,
not the session lock.
The session lock is for diagnosing *incorrect-usage*.
The catalog lock is the truly load-bearing lock for preventing partial transactions.

**As implemented (`locks.py`).** The lock hierarchy lives in its own
module rather than in `safety.py`, but follows the "by design" aspiration: a
process-global `_state` tracks a monotone lock *level* and each acquire function
asserts it is not run out of order (`_L_NONE < _L_MACHINE < _L_SESSION <
_L_MACHINE_EXCL < _L_CATALOG`; a tool may skip levels but never go backwards).

* `locks.acquire_machine_shared()` — called first thing in `main()` (after
  `safety.arm()`), before argparse, so every invocation yields the machine to a
  profiler holding it exclusively.
* `locks.acquire_session(catalog_dir, session_id)` — exclusive, non-blocking;
  raises `DhHlError` with the concurrent-session-use message on `BlockingIOError`.
* `locks.upgrade_machine_exclusive()` — `LOCK_UN` then `LOCK_EX` on the same fd
  (release-then-reacquire, per the objection analysis above).
* `locks.acquire_catalog(catalog_dir)` — exclusive, blocking; the load-bearing
  lock, held through rollback.  Records the abspath in `_state["catalog_dir"]`.
* `locks.catalog_lock_held()` / `locks.locked_catalog_dir()` — the pair backing
  the **Catalog lock invariant** (below).

Lock fds are stored in `_state` and **never closed**, so the OS releases them at
process exit, strictly after the `atexit` rollback (rollback thus runs with the
catalog lock still held). Lock files and the machine/`handles/` dirs are shared
infrastructure created directly via `os.makedirs(exist_ok=True)` / `os.open`
with `O_CREAT` (no `O_EXCL`), deliberately **not** tracked by `safety.py` — they
must survive rollback and be shared across processes.

Wiring: `main()` takes the shared machine lock; `Context.for_catalog` /
`for_session` acquire the catalog lock (and, for `for_session(session_lock=True)`,
the session lock) via `Context._open_catalog`, which acquires **then** constructs
the `Catalog`.  `build` drives the acquisitions directly.
`exec`/`exec_exclusive` use the machine lock (+ upgrade).

**Catalog lock invariant (load-bearing).** Possessing a `Catalog` object — or any
sub-object reachable from it (`ScheduleNode`, `IdeaNode`, `SessionNode`,
`Commentary`, …) — *guarantees* the catalog lock is held **for that catalog**.
`Catalog.__init__` asserts `catalog_lock_held() and locked_catalog_dir() ==
self.catalog_dir`, and `flush()` re-asserts it; there is **no** exemption (the
earlier "assert only in a locked run" compromise was rejected in favor of this
hard guarantee).  The catalog lock is never released mid-process (only the
machine lock is, during the exclusive upgrade), so the guarantee holds for the
object's whole lifetime.  Tests must hold the lock too — the invariant is never
skipped, only satisfied more cheaply; see "Tests" → *Locking in tests* for the
fixtures (`open_catalog`, `run_tool`, `fake_locks`) that make that ergonomic.

Because a `Catalog` implies the lock, `Catalog.__init__` does **not** acquire it;
the caller must (that is what `Context._open_catalog` and build do).  The
per-session `SessionWorkspace` is deliberately **not** a catalog sub-object: it
is constructed from a `catalog_dir` (+ optional `catalog`) and its reads
(`generator.cpp`, `current_idea_state.txt`, `bin/`) need no lock — this is what
lets `build`/`profile` read + compile the workspace before taking the catalog
lock.  A `catalog` is required only to *mutate* the current idea state
(`CurrentIdeaState.set_*` raises without one), which always happens under the
lock.


### Tool Safety: Tree Structure Invariants

Remember to check tree structure invariants whenever adding new edges.
You are responsible for ensuring the tree structure invariants are
not violated even if it's not explicitly spelled out as a failure mode of the tool.
Hence it's strongly advised to use a common helper function for checking and adding edges.

**As implemented:** there is not (yet) a single unified "add edge" helper; the
checks live in the `Catalog` (`catalog.py`) methods that create edges:

* `link_new_child_schedule(idea, schedule)` — enforces "idea's parent schedule
  strictly older than the child schedule" when a build attaches a new child
  schedule to an idea.
* `create_idea(parent_schedule, ...)` — enforces "parent of an idea is a major
  schedule" (`parent_schedule.is_major()`) and rejects proposal-name collisions.
* `reparent_existing_schedule(idea, schedule)` — the `fix_canonical` re-parent;
  re-checks the timestamp invariant.
* `force_parent_idea` (in `tools.py`) checks root-ness / no-existing-canonical /
  timestamp inline before calling `ScheduleNode.set_parent_existing_root`.

Session edges:

* `create_session` enforces "parent session strictly older than the child"
  (skipped when there is no parent session).
* Depth rules are enforced by the creating tools rather than the model:
  `new_sub_session` makes a child at parent depth+1; `new_successor_session`
  requires a depth-0 self-closed session and makes a depth-0 successor.
* The derived `session_is_closed` walk *reads* the parent-older invariant to
  guard against infinite loops on a cooked catalog, and **raises** on a
  violation (see the note in that method / the History Tool's analogous guard).

FUTURE: consolidating all of these into one checked-edge helper is still
advisable if we end up adding more entry-points that add edges;
each edge-creating site currently checks inline.


### Tool Safety: Timestamp Conflicts

**Intra-process guard:** `Catalog.fresh_timestamp()` returns `ids.now_timestamp()`
(UTC, microsecond precision) busy-waited until it is strictly greater than
`self._last_timestamp`, the last value handed out *this process*. That guarantees
intra-process monotonic, distinct timestamps. It is one of the two guards the
mint helper below combines; the `profile` benchmark loop, for instance, mints each
benchmark name via `mint_timestamped_name`, so a machine fast enough to emit two
benchmarks in one microsecond simply spins until the clock ticks (and skips any
name another process already committed).

**Minting scheme for concurrency (implemented):** resolve uniqueness at
*mint time*, under the catalog lock, **uniformly for every timestamped catalog
name** — schedule dirs (`sch/{ts}_{hash}`), session dirs (`session/{id}`),
commentary (`comment/{ts}_{hash}.json`), benchmarks (`bench/{host}_{ts}.json`),
warning toggles (`warning_toggle/{ts}.json`), and benchmark sets
(`benchmark_sets/{host}_{ts}.json`). To
mint a name: busy-wait `fresh_timestamp()` for process-local monotonicity, then
`os.path.exists` the full candidate path; on a hit, re-mint and retry. Both
guards are needed and neither alone suffices: the busy-wait separates names
minted within one process run (a multi-node creator like `new_catalog` mints
several before any flush, so they aren't on disk yet), and the `stat` separates
them from names another process already committed (the catalog lock guarantees
those are on disk before we mint). One `stat` per name, no enumeration of the
timestamp population.

This is implemented as `Catalog.mint_timestamped_name(build_path)` in
`catalog.py`, where `build_path` maps a candidate timestamp to the absolute path
whose uniqueness must hold. Its callers: `Catalog.create_schedule` (mints over
`sch/{id}`), `ScheduleNode.add_commentary` (over the `comment/{ts}_{hash}.json`
file), `ScheduleNode.add_benchmark` (over `bench/{host}_{ts}`), and
`ScheduleNode.add_warning_toggle` (over `warning_toggle/{ts}.json`), and
`Catalog.create_benchmark_set` (over `benchmark_sets/{host}_{ts}.json`).
`add_benchmark` now mints internally and no longer takes an explicit timestamp
argument — `build.py`'s profile loop just calls
`node.add_benchmark(file_hostname, bench_obj)` (sanitized hostname for the file
name). Session-dir minting routes through the same helper via
`Catalog.mint_session_id`.

**We deliberately standardize on this** even for names whose timestamp does not
propagate into an ID (`comment`, `bench`), rather than special-casing them to
lean on the `O_EXCL` create alone. Rationale: the "does this name propagate into
an ID?" distinction would otherwise have to be re-audited every time a new ID
cross-reference is added to the catalog schema — far more likely, for a
prototype, than ever wanting to relinquish the always-held catalog lock that
makes the uniform `stat` correct. The cost is one redundant `stat` on the
non-propagating names; negligible.

Correctness rests on a single invariant: **minting a catalog name happens only
while the catalog lock is held**, held continuously through the create. Assert
this in the mint helper (this is the "assert the lock is held" aspiration in
Lock Hierarchy, made concrete). Given it, the subsequent `O_EXCL`/`os.link`
create *cannot* collide, so a `FileExistsError` at create time is a "can't
happen" harness bug — raise it (→ rollback), never retry. Net effect: exactly
one mint path, and zero create-time retry branches.

Idea nodes are outside this scheme: their ID is `{proposal}_{parent}` with no
timestamp, so their uniqueness stays the proposal-name-collision check in
`Catalog.create_idea`, unchanged. (`fix_canonical` builds a `fix_canonical_{ts}`
proposal name from `fresh_timestamp` for readability, but its uniqueness is
still the idea-node collision check, so it does not use the mint helper.)


# Tool Internal Design

**Codebase map** (`dendritic_hl/dendritic_hl_lib/`):

* `main.py` — argparse. `_build_parser()` builds the subcommands (each takes
  `-C`/`-s`); `COMMAND_HELP` (name→one-liner) drives `help`; `_DISPATCH`
  (name→`cmd_*`) routes. `main()` calls `safety.arm()`, acquires the shared
  machine lock, intercepts `exec`/`exec_exclusive`, dispatches, and turns
  `DhHlError` into a stderr message + exit 1.
* `errors.py` — `DhHlError` (user-facing; exit 1, triggers rollback) and
  `HarnessError` (subclass; build-environment problems).
* `ids.py` — pure ID/timestamp/hash helpers for schedule, idea, and session IDs
  (`make_*_id`/`is_*_id`/…, plus `sanitize_component` for session user/host).
* `safety.py` — the rollback/overwrite/commit registry (see File Rollback).
* `locks.py` — the machine directory, the flock lock hierarchy, and the lock-free
  session-handle store (see Lock Hierarchy, Session Handles).
* `catalog.py` — the in-memory model (conceptual description below). `_UNLOADED`
  sentinel; sub-objects `Commentary`, `Benchmark`; nodes `ScheduleNode`,
  `IdeaNode`, `SessionNode`; the `CurrentIdeaState` parser; and the top-level
  `Catalog` (lazy `schedules`/`ideas`/`sessions` dicts, derived child-edge
  linking, dirty set + `flush()`, `mint_timestamped_name`/`mint_session_id`,
  `create_schedule`/`create_idea`/`create_session`, the edge helpers, and the
  `session_is_closed`/`session_is_terminus` predicates). `Catalog.__init__`
  enforces the Catalog lock invariant (see Lock Hierarchy). Short-ID
  resolution/formatting are free functions exposed via `Catalog.resolve_*` /
  `format_*`.
* `context.py` — `resolve_target` maps `-C`/`-s` (handle or full ID) to
  `(catalog_dir, session_id)`; `Context.for_catalog` / `for_session` acquire the
  locks then wrap a `Catalog`; `SessionWorkspace` is the (catalog-free-readable)
  per-session private workspace owning `generator.cpp`, `current_idea_state.txt`,
  `bin/`; `finish()` = `catalog.flush()` + `safety.commit()`; `read_text_or_stdin`
  handles `-` and turns a missing file into a clean `DhHlError`.
* `tools.py` — every non-build `cmd_*` (catalog/idea/session queries + session
  lifecycle) plus shared print/JSON helpers.
* `build.py` — `cmd_init_build` / `cmd_build` (see the Build Tool in idea.md),
  with the toolchain steps behind the monkeypatch seams `_write_ninja`,
  `_ninja_build`, `_discover_generator_name`, `_emit`, `_link`, `_run_benchmark`
  (see Tests).

Obviousness and idiot-proofing are priorities for this prototype since
this design may evolve quickly and isn't meant to scale to production uses.
I'd like to have most of the harness code working with an in-memory representation
that's fairly 1:1 with the conceptual state.

Each tool execution is short-lived and breaks into multiple phases

* Lazily load the needed parts of the catalog to memory.
* Modify state in-memory (can be interleaved with lazy loads).
* Flush changes to the catalog directory: create new files/directories,
  and queue overwritten files (`safety.queue_overwrite`).
* Actually overwrite files (`safety.commit`).

There is a top-level `Catalog` object, owning
* A `Dict[str, IdeaNode]`: idea nodes by full ID
* A `Dict[str, ScheduleNode]`: schedule nodes by full ID
* A `Dict[str, SessionNode]`: session nodes by full ID

Each object
* is accessed with getters and setters
* has initially empty state, and is lazily initialized from disk when needed by getters
* is dirtied when modified, or upon creation if it's not loaded from disk;
  do this in each setter and non-load-from-disk `__init__` path;
  DON'T ever expect outside code to dirty an object manually!
* has `flush` callbacks that uses the `safety` module to write changes to disk.
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


# Tests

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

**Monkeypatch seams (init_build/build).** `tests/test_build_fake.py` exercises
the `init_build`/`build` orchestration without a real Halide toolchain by using
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

**Locking in tests.**

*What the next person needs to know (the short version):* a `Catalog` may only be
constructed while its catalog lock is held (see "Tool Safety: Lock Hierarchy" →
Catalog lock invariant), so a test that constructs one at lock level NONE hits an
`AssertionError`. Do **not** disable the assert. Instead:

* Driving a **tool** (`cmd_*`, `init_build`/`build`) in-process? Call it through the
  `run_tool` fixture: `run_tool(tools.cmd_new_idea, sess.ns(...))`. It models one
  process invocation (reset lock state → take the shared machine lock → run), so
  the tool's own `Context.for_*` acquires the catalog/session locks normally.
* Building a **catalog directly** (pure-model tests)? Use `conftest.open_catalog(dir)`
  instead of `Catalog(dir)`. It marks the lock held for that dir first.
* A **catalog+session** to work in? Use the `session` fixture (a `Sess` handle
  with `.ns(**kw)`, `.write_workspace(text)`, `.catalog_dir`, `.session_id`); it
  builds a consistent seeded catalog. Subprocess tests reuse it to bootstrap,
  then drive `./dh_hl` with `-C`/`-s`.

If you see a baffling "Catalog constructed without holding its catalog lock"
failure, it means one of the above was skipped.

*The infrastructure (how it works):*

* `reset_safety` (from the original harness) — clears `safety`'s process-global
  rollback/overwrite registry between tests.
* `_reset_lock_state` (autouse) — returns `locks._state` to level NONE (and
  clears the trace sink) before and after every test, so a stale held-lock from
  one test cannot let the next construct a `Catalog` it shouldn't.
* `open_catalog(dir)` — calls `locks._fake_hold_for_tests(dir)` (sets `_state` as
  if machine+catalog(dir) locks were held, with **no** `flock`/filesystem) then
  returns `Catalog(dir)`. For code paths that never go through the real acquire
  path but still must honor the invariant.
* `fake_locks` — monkeypatches `fcntl.flock` → no-op and `locks._open_lock_file`
  → a dummy fd, leaving the real `acquire_*` bodies to run (so the monotone-level
  **ordering asserts** and `_state`/`locked_catalog_dir()` bookkeeping are
  genuinely exercised, minus the syscalls). `run_tool` depends on it.

*Listening in on lock behavior.* `locks._trace(event)` appends to `locks._trace_sink`
when a test sets it to a list (a no-op otherwise). Under `fake_locks`, each
`acquire_*` records `("machine","shared")`, `("session","exclusive")`,
`("machine","exclusive")`, `("catalog","exclusive")` in order, so a test can
assert the exact per-command lock sequence — e.g. that `build --profile`
upgrades the machine lock to exclusive before taking the catalog lock and a
non-profiling `build` does not, or that a read-only tool skips the session lock. This is white-box coverage the
subprocess tier cannot observe; real cross-process mutual exclusion is instead
covered by the subprocess timing test (`test_locks.py`). (`run_tool` resets the
sink per call, so `locks._trace_sink` reflects the most recent command.)
