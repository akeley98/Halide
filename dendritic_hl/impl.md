# Implementation Notes for Dendritic Halide Harness (dh_hl)

These are the implementation-side companion notes to [idea.md](idea.md) (the
behavior contract).  Keep the two in sync when changing a tool.

For now this tool is an early prototype and backwards compatibility is a non-goal.
So do not worry when implementing changes that would break reading old catalogs.

NOTE TO AGENTS: the `halide`-marked tests (which build real Halide pipelines
against the local `~/Halide` build) CAN be run in this environment.  Do not
conclude the Halide build is unavailable and skip them -- it is present on both
"David's MacBook Pro" and the "MantissaAmpere" Linux box.  Run them with
`.venv/bin/python -m pytest` (no `-m "not halide"`), and prefer real end-to-end
coverage over faking build artifacts where a `halide` test is feasible.

IMPL TASK: paragraphs like these highlight where the doc describes features
not yet implemented in the actual code (that's the agent's job).
When you're reasonably confident the task is done, delete the IMPL TASK paragraph.
Leave them in if there's significant doubt,
or clarification is required from the user.
Avoid extra blank lines after removing IMPL TASKs
(2 blank lines at end of markdown sections,
1 blank line for all other paragraph breaks).
Also please stop referencing these tasks in tests/comments/etc.
as these references are DOA once the task gets erased (and confuse greppers).
PS ignore concurrent changes in `human_stuff/` and don't commit them.

NOTE: I got tired of the split of per-tool information between
`idea.md` and `impl.md`, so now the tool-specific implementation information
is in `idea.md` only.
These things are bracketed by comments like these:
<!-- impl -->
(stuff in here is stripped out from the `dh_hl prompt`/`dh_hl help` output).
<!-- end impl -->
Obviously you can't see the comments if you're not looking at Markdown source.


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

The top-level catalog directory contains sub-directories for each node/object type:

* `idea`
* `sch`
* `session`
* `benchmark_sets`
* `golden`
* `problem`

as well as

* `private` directory
* `.gitignore`, ignores `private`

and undocumented experiment state under an `experiment` subdirectory,
new files written write-once by `dh_hl experiment begin`:

* `experiment/begin_timestamp.txt`, timestamp + newline set by `dh_hl experiment begin`
* `experiment/label.txt`, label + newline set by `dh_hl experiment begin`

The write-once rule (`safety.new_file` with the default exclusive create) makes
`begin` fail on a second call, so the label/timestamp are recorded exactly once.


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
  holding `unknown`, `c++ error`, `halide error`, or `success` (the `Result`
  enum).
  The default value is `unknown`.
  Ranked worst-to-best by the `Result` definition order; `build` only ever moves a
  node to a better value (`catalog.best_result`).
  An **absent** `result.txt` is the normal unbuilt state and reads as `unknown`
  *silently*.  A **malformed** `result.txt` (e.g. a merge conflict left markers in
  it) also degrades to `unknown`, but with a `stderr` warning — mirroring the
  Problem `state.txt` leniency.  This is safe: `best_result` only moves a node
  upward and `canon` requires `success`, so a spurious `unknown` is merely
  pessimistic, and a rebuild overwrites it (`set_result` always dirties).  Merely
  *reading* the malformed value does not dirty the node, so it is not silently
  rewritten.

* **Benchmark Sub-objects:** store in `bench/{hostname}_{timestamp of benchmark}.json`
  (the `{hostname}` here is the *sanitized* stable hostname — see "Stable Hostname").

  The `{hostname}_{timestamp of benchmark}` part is the benchmark's *local ID*
  (exactly the file-name stem), so don't parse files to resolve IDs.  The
  benchmark's *full ID* prepends the parent schedule full ID
  (`{parent schedule full ID}_{hostname}_{timestamp}`).  Because the timestamp is
  fixed width and the schedule prefix is fixed width, the hostname in the middle
  parses out unambiguously even though it may itself contain `_`.  The local ID is
  NOT the short ID: benchmark short IDs are the session-scoped
  `private.{schedule}.{i}.{n}` form (see "benchmark short ID translation" below),
  unrelated to the local ID.  Implemented as the `Benchmark` class in
  `catalog.py`; full-ID resolution is the `_resolve_benchmark` free function
  (exposed via `Catalog.resolve_benchmark`) -- there is no catalog-level short-ID
  resolver or formatter.  The benchmark JSON gains a `warnings` list (see idea.md
  "Benchmark Sub-object State" and the `HL_PROFILER_JSON_TEMPORARY_WARNINGS` note
  in `reference_build_commands.md`).

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

**"Workspace Files" Note:** this term refers only to the C++ generator
(`generator.cpp`) and generator parameters files
(`generator_parameters.json`), as these are the only data explicitly
exposed as files to the harness user.
All other state is technically files, but not "workspace files".

*Merge risk:* `parent.txt` merge conflict if two branches retroactively parented
a root schedule node to two different idea nodes.
No automatic fix provided: this power should be used very sparingly anyway.

*Merge risk:* `result.txt` conflict.
Unlikely but could happen due to committing a failed build that
later worked due to trying different generator parameters.
No automatic *resolution* is provided, but the conflict does not crash: a
malformed `result.txt` degrades to `unknown` with a `stderr` warning (see the
Result bullet above), and the next `build` overwrites it with a real value.

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

* **ID:** directory name.

* **Prompt:** `prompt.txt`

* **Parent:** `parent.txt` holds a session node full ID plus a newline,
  unless there is no parent, in which case this file doesn't exist.

* **Seed Ideas:** `seed_ideas.json` holds a JSON list of idea node full IDs
  (>= 1; the 0th is the canonical "the session's seed idea").

* **Default Anchor Schedule:** if it exists, its full ID plus a newline is in
  `default_anchor_schedule.txt`

* **Golden Schedule Node on Opening:**
  if it exists, its full ID plus a newline is in
  `opening_golden_schedule_node.txt`

* **Enabled Problems on Opening:**
  `opening_enabled_problems.json`, list of problem object full IDs.

* **Outputs:** `outputs.json`, doesn't exist if no outputs yet; see next section

* **Delisted Flag:** Delisted iff `delisted.txt` exists; contents are ignored.

* **Depth:** implied from the ID; parse all digits before the first `_`.
  Note, the depth will always be formatted as-if by `%d`
  (base 10, no redundant leading 0s etc.)

* **Timestamp:** implied from the ID

*Merge risk:* `outputs.json`, no automatic fix provided.

**Username/hostname sanitization (implemented, `ids.sanitize_component`):** each
of `username` (from `getpass.getuser()`, falling back to `"user"`) and `hostname`
(from `socket.gethostname()`) has every character outside `[A-Za-z0-9_-]` mapped
to `_`, is truncated to 64 chars, and is never empty (an all-stripped value
becomes `"_"`). De-anonymizing is intentional, so there is no hashing. The `@`
between them is therefore the unique separator, and since the timestamp is fixed
width, `looks_like_session_id`/`session_depth`/`session_timestamp` parse the ID
unambiguously (`_SESSION_ID_RE` in `ids.py`).


### Session Outputs on Disk (outputs.json)

A JSON object:

    {"schedules": [{"id": <schedule full ID>, "pool_tag": <str>}, ...],
    "benchmark_sets": [<benchmark set full ID>, ...]}.

The schedules are ordered (the first is the *primary* output).

Owned by `SessionNode` in `catalog.py` (`set_outputs`,
`output_schedule_ids`, `output_schedule_pool_tags`,
`output_benchmark_set_ids`).


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


### Golden Objects on Disk

Stored in `golden/{timestamp}/golden.json` as a JSON object in the
same format as `json_golden_info`.

*Merge risk:* low, microsecond timestamp collision with non-equal golden objects


### Problem Objects on Disk

Stored in `problem/{full id}/` as multiple files

* `argv.json`, JSON list of CLI strings.
  The hash of the UTF-8 encoded JSON is the full ID.

* `state.txt`, state as string + newline.
  Interpret any malformed `state.txt` as if it were `enable`,
  with a warning printed to `stderr`.

* `short_name.txt`, short name + newline

*Merge risk:* `cli.json` cannot have a merge problem except for full SHA256 collisions.
`state.txt` and `short_name.txt` can fail due to mutability,
but this is easy to fix by using public state/short name setters -- and
genuinely so: those setters dirty *unconditionally* (no "skip if unchanged"
short-circuit), so setting a malformed file to any value, including the one it
already resolves to, rewrites it (see "A cautionary tale" in Tool Internal Design).


### Session Private Workspace

Inside the `private/{session id}` sub-directory, there is

* `session.lock`, lock file (contents ignored)

* `generator.cpp`, workspace C++ file

* `generator_parameters.json`, workspace generator parameters file

* `current_idea_state.txt`, current idea state

* `halide_path.txt`, path to Halide directory + newline
  (empty or absent file if not set yet).  Modelled by the `HalidePath` object
  (see "Private-workspace state objects"); read lock-free by `build`, set by the
  `set_halide_path` tool.

* `bin/` directory

* `current_anchor_schedule.txt`, full ID of schedule node and newline.
  An empty *or* absent file both mean "no current anchor schedule" (we never
  delete files, so clearing it writes empty).  Modelled by the `CurrentAnchor`
  object (see "Private-workspace state objects").

* `benchmark_short_id/{schedule node full ID}/{generator parameters index}.json`
  backs the `private.{schedule}.{i}.{n}` benchmark short ID form (idea.md
  "Benchmark short ID").  Each file is a JSON list of the benchmark full IDs THIS
  session created for that (schedule, generator parameters index) pair, in
  creation order; `n` is that 0-based index, and a missing file is the empty
  list.  The **sharding is load-bearing at scale**: a hard Halide campaign can run
  hundreds of thousands of benchmarks, but any one operation only ever touches a
  handful of schedules -- the set being profiled (append), or the one schedule
  named by a short ID being resolved.  One-file-per-pair keeps the O(n) cost
  *per schedule* instead of loading/rewriting the whole session's benchmark
  database.  It is also why there is **no** reverse full-ID -> short-ID lookup (it
  would scan every shard) and no `benchmark_short_id` getter: the only formatter
  is `build`, which already knows the exact `(schedule, params index, n)` from
  `record` and formats directly.  Modelled by the `PrivateBenchmarkShortIds`
  object (see "Private-workspace state objects").  Helpers:
  `SessionWorkspace.record_benchmark` (append, returns `n`),
  `format_benchmark_short_id` (a pure static formatter over an explicit
  `(node, i, n)`), and `resolve_benchmark_short_id` (parse + single-shard
  `lookup`); `Context.resolve_benchmark_arg` is the user-facing entry point (full
  ID via the catalog, `private.` form via the workspace).

* `private_ideas.json`, the session private idea list (a JSON object).
  The keys are the set of idea node full IDs comprising the list.
  The values are the pool tags (strings).  Unordered; the cost is not stored
  here, it's derived when needed.  Modelled by the `PrivateIdeaList` object
  (see "Private-workspace state objects" below); `SessionWorkspace` exposes it
  through `read_private_ideas` / `set_pool_tag` / `get_pool_tag` /
  `hide_private_idea` / `rename_pool_tag` / `remove_private_idea`.

* `private_benchmark_sets.json`, the session private benchmark set list.
  The keys are the set of benchmark set full IDs comprising the list.
  The values have cached benchmark info (documented below).  Modelled by the
  `PrivateBenchmarkSetList` object; `SessionWorkspace.add_private_benchmark_set`
  / `remove_private_benchmark_set` are the exposed helpers.

* `init_build.json`, left behind by `dh_hl init_build`: the catalog-relative
  `generator.cpp` + `generator_parameters.json` paths of each schedule node to
  build (target/other/anchor).  This lets `build` compile without first
  acquiring the catalog lock -- `init_build` is a hack to make that locking
  easier.  Its format is documented in `build.py` (`_INIT_BUILD_FILE`), not
  here, as it's not of general interest.

**Private-workspace state objects.** The mutable private-workspace state follows
the same lazy-load-once + dirty + flush discipline as the catalog nodes (see
"Tool Internal Design"); there is deliberately **no** bare "read the file each
call" code.  `PrivateIdeaList` and `PrivateBenchmarkSetList` (a shared
`_PrivateMapState` base) and `CurrentAnchor` / `HalidePath` (single-value text
files) each lazy-load their file **once**
into memory (`_UNLOADED` sentinel), mutate in memory, and register on the catalog
dirty set (`catalog._mark_dirty`) so `catalog.flush()` writes them **once** —
exactly like `CurrentIdeaState`.  `SessionWorkspace` owns one of each (lazily
created) and its `read_*` / pool-tag / benchmark-set methods are thin
delegations; `read_private_ideas` / `read_private_benchmark_sets` return a
**read-only `MappingProxyType` view** of the live map (mutating it raises, the
nearest Python has to a `const&`, so state can only change through the objects'
own dirty-tracking methods — never a stray outside mutation; `join_session`
snapshots with `dict()` before mutating).  This is what makes a looped mutation correct — e.g.
`join_session` adding several benchmark sets, or `new_sub_session` setting
several pool tags, accumulate in the one in-memory map and flush together
(the previous re-read-per-call code persisted only the last write).  `init_workspace`
is the exception: as a pure initializer it writes every file directly with its
`--force` `allow` flag (immediate `O_EXCL` refuse), bypassing these objects.

`PrivateBenchmarkShortIds` follows the SAME discipline but is **not** a
`_PrivateMapState`: it is a *sharded* directory (one JSON list per (schedule,
params index), lazy-loaded per shard, dirty tracked per shard, each dirty shard
flushed once).  It applies the same anti-leak principle without a `view`: it has
no bulk reader, so the mutable list never escapes -- `record` is the sole mutator
(catalog-check first, append, dirty, with no failure point between the append and
the dirty) and `lookup` returns a single immutable string.  A future bulk reader
would have to return a copy/tuple, never the live shard list.

Any command that needs `private/{session id}` creates the *directory* lazily
(`SessionWorkspace.ensure_private_dir`; the `session.lock` and `bin/` likewise).
The *content* files above are written by `dh_hl init_workspace` (the blessed
initializer) or by the `restore_*` tools — session creation no longer initializes
them, so a freshly created session's workspace is empty until `init_workspace`
runs.  The whole `private/` tree is gitignored, so it can desync from the
git-tracked `session/` state after a git checkout.


### Private Benchmark Sets on Disk

Each value stored in the `private_benchmark_sets.json` object includes
key/value pairs:

* `hostname`: string, `hostname` of each referenced benchmark.

* `cpu_count`: number, `cpu_count` of each referenced benchmark.

* `profiler_version`: number, `profiler_version` of each referenced benchmark.

* `problem`: string, `problem` of each referenced benchmark.

* `schedules`: object, keyed by schedule full ID; each value is a list indexed
  by parameters index, whose entries are objects `{"wall_time_min": [...],
  "id": [...]}`.  Both lists are of length batch-count and give the per-batch
  `wall_time_min` and benchmark full ID, respectively.

FUTURE: either warn or do something intelligent when mixing benchmarks
from different computers.

The caching allows quick implementations of perf critical queries:

1. Find the relevant benchmark sets for a given schedule
   (by scanning the keys in `schedules`).

2. Do basic cost comparisons using the cached `wall_time_min`.

Only the more detailed profiler report tools require reading the
actual benchmark sub-objects.

**Implemented** as `context._benchmark_set_cache`, called by
`SessionWorkspace.add_private_benchmark_set(set_id, catalog)` (the sole add
helper, so the cache is always populated).  It reads the `BenchmarkSet` JSON and
each referenced `Benchmark` sub-object under the catalog lock — every caller
(the `build` profile phase, `join_session`, the `add_private_benchmark_set`
tool) already holds it.  The `hostname`, `cpu_count`, `profiler_version` must be
identical across every benchmark in the set (one machine, one profiling run);
this is asserted at population time.

Wrong `profiler_version`: the cache records the set's actual version verbatim
rather than rejecting it at add time (an agent may legitimately hold a set from
before a profiler bump).  The version gate is the shared `cost.compatible_sets`
helper (used by both `cost.CostData.from_private_sets` and the profiler-stats
reachability walk): it *skips whole sets* whose cached `profiler_version`
differs from `catalog.EXPECTED_PROFILER_VERSION` (the single expected-version
constant), and — crucially — **warns to stderr naming the discarded set**, so a
version bump doesn't silently turn every cost into `null` with no explanation.


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
  link the standalone binary (phase 4), and, when `--profile` is on, run the
  benchmark.  This per-param-set work is a Python `for` loop; don't push the
  loop into ninja. This keeps David from getting paranoid about unexpected
  parallelism (yes, I know about pools).

The steps performed are:
* compile the C++ workspace file to a Halide generator executable (ninja),
  and the shared `RunGenMain.o` (ninja)
* emit the standalone `halide_runtime.o` once per bin/ (Python; node/param
  independent)
* run the generator to emit the pipeline as a `no_runtime` **object**, plus the
  header, `registration.cpp`, and the `.stmt`/`.conceptual.stmt` files, using
  `target=host-profile-no_runtime` (Python)
* link the RunGenMain binary from `RunGenMain.o` + `registration.cpp` + the
  `no_runtime` object + `halide_runtime.o` (Python)
* link the `no_runtime` object into a shared library `dh_hl_pipeline.{so,dylib}`
  (Python) for external dlopen runners

The pipeline is emitted ONCE (the `no_runtime` object) and feeds both the
RunGenMain and shared-library links -- see the "-f layout" section above for the
per-(node, i) subdirectory + stable-symbol details, and the
[Reference Build Commands](reference_build_commands.md) file (Path A / Path B) for
the tested recipes.  When profiling, keep the per-node generator executable from
phase 1 and re-run the emit + link + benchmark steps for each parameter set.
(`build` reads the node source/params from the catalog files named by
`init_build.json`, not the workspace directly.)

**Generator name.** `GenGen` still requires `-g <name>` even when only one
generator is registered, but under the single-generator assumption (see Goals)
the name is discovered automatically: run the generator executable with **no**
`-g`, and it errors out listing `available Generators are:` followed by the sole
registered name; scrape that single name and pass it as `-g`.

**Output basename (`-f`) and per-(node, params-index) layout.** A `build` run
compiles many binaries at once (up to three nodes × their parameters objects),
and everything the generator emits for one build (`dh_hl_pipeline.a`,
`.registration.cpp`, the `c_header`, `.stmt`/`.conceptual.stmt`, the shared
library, the serialized `algorithm_hlpipe`) shares the `-f` basename.  Rather
than fold (node, parameters index) into the basename, we **isolate each (node,
parameters index) in its own subdirectory** `bin/{node.full_id}_{i}/` and emit
into it with a **fixed, stable** `-f dh_hl_pipeline`.  The subdirectory supplies
the uniqueness (no clobbering across nodes or params), which frees the basename
to be the *same clean symbol for every schedule node*: the generated header is
always `dh_hl_pipeline.h` declaring `dh_hl_pipeline(...)`, and the emitted shared
library always exports `dh_hl_pipeline`.  That stability is load-bearing — it is
exactly what lets `copy_build_output header`/`shared_library` and a `dlopen`
runner be one-and-done (idea.md "New Problem Tool" custom-runner setup and "Copy
Build Output Tool"), instead of the runner having to track a per-node symbol.
`dh_hl_pipeline` is already a valid **C identifier** (Halide bakes `-f` into the
symbol names in `registration.cpp`), so no sanitization is needed.

The **param-independent** artifacts stay at the `bin/` root, one per node — the
ninja file `bin/{node.full_id}.ninja` and the generator executable
`bin/{node.full_id}_generator` — plus the fully shared, node- and param-
independent `RunGenMain.o`.  Only the phase-2 emit + phase-4 link outputs (and,
for a `<Lib>` problem, the shared-library emit) land in the per-(node, i)
subdirectory.

Consequence for provenance: the profiler-JSON `name` field is now the constant
`dh_hl_pipeline` for every pipeline, so it can NOT identify which node actually
ran.  That is fine because provenance verification is deliberately deferred (the
idea.md Build FUTURE note and reference_build_commands.md "separate baked
provenance field"); until the Halide change lands there is no in-`name` hash to
check.  (Design history: a single fixed `-f dh_hl_gen` in one flat `bin/` dir
clobbered across nodes/params and was replaced by a per-(node, i) sanitized
basename `g_{sanitized full_id}_{i}`; that in turn is now superseded by the fixed
name + subdirectory here, which was needed to give the runner/header a stable
symbol — see idea.md Build Tool "problem 2".)

**As implemented** (`build.py`): `_param_subdir(full_id, i)` -> `bin/{full_id}_{i}/`
and `_emit`/`_link` run with `-o {subdir}` / `-f dh_hl_pipeline`; the node-level
ninja file, generator exe, and shared `RunGenMain.o` stay at the `bin/` root.
`stmt`/`conceptual_stmt` are emitted for every built (node, i) and fetched on
demand by `copy_build_output` (`_build_output_rel` maps a `what` to its bin/
path); there is no eager copy-to-`bin/{i}.stmt` and no path printing anymore.
The pipeline is emitted ONCE per (node, i) as a `no_runtime`
**object** (`-e object,... target=host-profile-no_runtime`), and both binaries
link from it: `_link` builds the RunGenMain `.rungen` (object + the shared
`bin/halide_runtime.o` emitted once by `_ensure_runtime`), and `_link_shared`
builds the `no_runtime` `dh_hl_pipeline.{so,dylib}` (`-shared`, plus
`-undefined dynamic_lookup` on macOS) whose undefined `halide_*` bind upward to a
dlopen runner that owns the runtime.  One Halide compile feeds both paths, and
they are built together (idea.md Build Tool: "N shared library and RunGenMain
binaries are built").

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


# Cost Model Core

The shared computation behind `json_ranking_cost`, `json_compare_cost`, and the
`list_private_ideas` frontier lives in `dendritic_hl_lib/cost.py`.  It is
catalog-agnostic: `CostData.from_private_sets` consumes the cached benchmark-set
statistics (see "Private Benchmark Sets on Disk") and the tools translate its
plain-dict results into JSON.  This keeps the statistics in one tested place and
lets the frontier reuse the exact `json_compare_cost` logic the docs describe it
in terms of.  The approach and its bootstrap primitives were ported from the
reference campaign tooling (`tmp_bench_tools/bench_analyze.py`).

**Batch identity.** A "batch" (idea.md "Cost Comparison Methodology") is one
interleaved profiling round.  Its key is `(benchmark set full ID, batch index)`,
because only schedules profiled *together in one build run* share drift; two
schedules are "in the same batch" only when the same set measured both at the
same batch index.  Sets whose cached `profiler_version` isn't
`catalog.EXPECTED_PROFILER_VERSION` are dropped whole, with a stderr warning
naming the set (the shared `cost.compatible_sets` gate — see "Private Benchmark
Sets on Disk").

**Raw cost + representative.** The raw cost of one (schedule, parameters index)
in a batch is its `wall_time_min` (robust: the fastest of a record's runs, ~1%
CV; the mean is outlier-contaminated).  A schedule's *representative* for a
method is the parameters index with the lowest median raw cost over that
method's relevant batches (ties → lower index, deterministic).

**Making the 2-way CI precise.** idea.md says "compute the X% CI of the per-batch
differences."  Concretely: pair by batch — for every batch containing both
schedules, form `cost(LHS_rep) − cost(RHS_rep)` — then take a **percentile
bootstrap CI of the *median* of those paired differences** (`paired_diff_ci`:
resample the differences with replacement `B` times, take the median of each
resample, and read off the `(1−ci)/2` and `(1+ci)/2` percentiles).  Pairing
cancels common-mode drift, which is the whole point of interleaved batches;
marginal (unpaired) CIs would ignore it and over-flag.  A CI strictly below zero
⇒ `improvement` (LHS cheaper), strictly above ⇒ `regression`, straddling zero or
undefined (< 2 batches) ⇒ `unknown`.

**Determinism.** The bootstrap uses a fixed-seed local `random.Random`, so equal
inputs always yield an equal CI and thus an equal verdict.  That reproducibility
is what makes the cost tools testable from synthetic benchmarks (no Halide); it
is preferred over marginally higher precision from reseeding.  `B` defaults to
`cost.DEFAULT_BOOTSTRAP` (reduced from the reference's 20000 because the frontier
runs many pairwise checks in the agent's interactive loop, and a few thousand
resamples already give a stable percentile at these sample sizes); the same `B`
is shared by `json_compare_cost` and the frontier.


# Tool Safety Requirements

We require `flock` file locking to make concurrent harness usage safe.

Tools can assume sha256 collisions never happen.

Tools NEVER overwrite or modify existing files, except for:

* tmp files
* private session workspace files
* `result.txt`
* problem object state
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

* `new_file(path, data, *, overwrite_allowed=False)` is the single write helper.
  By default (`overwrite_allowed=False`) it creates a file with `O_CREAT|O_EXCL`
  and records it — the norm, since almost all catalog files are write-once and
  the `O_EXCL` is a hard guard.  `new_dir(path)` / `makedirs_tracked(path)` create
  directories, recording only the levels actually created. All recorded entries go
  on `_new_entries` (a LIFO list of `("file"|"dir", path)`).
* `new_file(path, data, overwrite_allowed=True)` is the mode for the
  allowed-to-change files: it exclusive-creates + records when the target is
  absent (so rollback can remove it) and otherwise defers an overwrite via
  `queue_overwrite`. Deferred overwrites are applied by `commit()` and are NOT
  rolled back.  (There is deliberately **no** separate `write_allowed` function —
  an earlier split into `new_file` vs. `write_allowed` confused agents, since
  "write_allowed" reads as a predicate rather than an action; the one flag on the
  one `new_file` is the whole story.)  `overwrite_allowed=True` with an *absent*
  target is how `init_workspace --force` writes fresh workspace state, while the
  default `overwrite_allowed=False` is how it refuses to clobber existing state
  (the `O_EXCL` create raises `FileExistsError`).
* `arm()` (called at the top of `main()`) registers the `atexit` handler
  `_rollback` and maps `SIGQUIT`→`KeyboardInterrupt`.
* `_rollback()` deletes `_new_entries` in reverse (files via `os.remove`, dirs
  via `os.rmdir`, so a created dir is empty when removed); it swallows `OSError`
  (drops that entry — no infinite loop) and retries the current entry on
  `KeyboardInterrupt`.
* `commit(*, assert_no_writes=False)` applies the deferred overwrites, then
  clears `_new_entries` so the still-registered `atexit` handler becomes a no-op
  — this is the "disable as the final step" that prevents rolling back a
  successful tool's effects.  `assert_no_writes=True` (used by `join_session
  --dry-run`) first asserts the run recorded no new files and queued no
  overwrites — a self-check that a dry-run mutated nothing.

The flush itself lives on the model objects, not `safety`: `Catalog.flush()`
calls every dirty object's `flush()` (see Tool Internal Design),
and `Context.finish()` is `catalog.flush()` then `safety.commit()`.
Test hook: `new_file` calls `_maybe_inject_failure()`,
which honors `DENDRITIC_HL_TEST_FAIL_AFTER` (see Tests).

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
  So can `main()`'s pre-argparse `init_build` selection invalidation
  (`build.invalidate_selection_best_effort`): it must run even when argparse is
  about to reject the invocation, and taking a *non-blocking, exit-on-failure*
  session lock there could itself fail -- exactly the low-level failure that must
  not defeat the guard. The remove is idempotent and session-private (the catalog
  lock, not the session lock, is the load-bearing one), so skipping it is safe.

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
(the workspace files, `bin/`) need no lock — this is what lets `init_build` read
the workspace, and `build` read + compile the catalog node files named by
`init_build.json`, before taking the catalog lock.  A `catalog` is required only to *mutate* the current idea state
(`CurrentIdeaState.set_*` raises without one), which always happens under the
lock.


### Tool Safety: Exception Safety

Generally exception safety isn't so important.
The policy is to roll back all changes anyway,
so leaving things inconsistent isn't a big deal.
Even file overwrites are largely safe, because they're deferred past
all the "business logic" by the `safety` module.
Some old code is written in a more complicated way due to exceptions,
but in hindsight I don't think this was needed (leave this old code alone
unless there's a reason to revisit and simplify).

The major caveat to this is build tool errors.
These are a catalogued bad outcome; the rollback does not happen.
Hence, the `HalideBuildError` class exists to distinguish these cases,
which require particular care.


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
mint helper below combines; the `build` profiling loop, for instance, mints each
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
argument — `build.py`'s profiling loop just calls
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


# Project Name for Collision Avoidance

**Policy:** wherever a dendritic_hl name shares a namespace with things outside our
control — and so a short/cute prefix could collide — spell the project out in
full as **`dendritic_hl`** (or `DENDRITIC_HL` for env vars).  The abbreviation
`dh_hl` is reserved for the *user-facing CLI* (the `dh_hl` command, the `dh_hl:`
banner lines, the `.dh_hl` catalog-dir suffix), where brevity is a feature and
there is no foreign namespace to collide with.

Concretely, use the full name for:

* **Environment variables:** `DENDRITIC_HL_TEST_FAIL_AFTER` (the safety
  rollback test hook), `DENDRITIC_HL_OUTPUT_LIB` / `DENDRITIC_HL_ALGORITHM_HLPIPE`
  (build → runner hand-off).  These live in the process environment alongside
  every other program's variables.
* **"Global" / machine-shared directory names:** the machine directory is
  `~/.cache/dendritic_hl/` (`locks.py`), which sits next to every other tool's
  `~/.cache` entry.
* **On-disk state tokens that could be mistaken for another format:** the
  current-idea-state wrapper `dendritic_hl_root(...)` / `dendritic_hl_idea(...)`
  (`catalog.py`).

Per-catalog, per-session files that live *inside* our own directories (e.g.
`sch/{id}`, `session.lock`, `init_build.json`) don't need the prefix — the
enclosing catalog/private directory already namespaces them.


# Enum Policy

The small, fixed vocabularies in the model are **`enum.Enum` types** (in
`enums.py`), not bare string literals.  Early code spelled every one of them as
raw strings (`"success"`, `"neutral"`, `"enabled"`, `"improvement"`, …) threaded
through the whole codebase; that made typos silent bugs, hid the valid set at the
use site, and blurred the line between an in-memory concept and its wire form.
The enums are:

* `Result` — schedule build result (`result.txt`); members declared worst→best,
  which *is* the `best_result` ranking.
* `Review` — a commentary's review; plus the derived-only `MIXED`.
  `COMMENTARY_REVIEWS` is the subset a single commentary may carry.
* `ProblemState` — a problem's `enabled`/`disabled`/`main` state (`state.txt`).
* `SideLink` — the `borrows_from`/`superseded_by` idea-side-link type.
* `CostVerdict` — a 2-way cost comparison's `improvement`/`regression`/`unknown`
  (JSON output only; never persisted).
* `IdeaStateKind` — the parsed `current_idea_state.txt` kind (`missing`/`no_idea`/
  `idea`/`conflict`).

**In memory, code passes and compares enum *members*, never the strings.**  The
strings live only on the **wire** — on-disk files, CLI arguments, and JSON output
— and we translate at that boundary:

* **Serialize:** `member.value` (e.g. `result.txt` ← `node.result.value`; a JSON
  payload ← `{"review": c.review.value}`).
* **Parse trusted input:** `SomeEnum(s)` / `SomeEnum.from_wire(s)` (the latter
  raises a `DhHlError` naming the valid values).
* **Parse possibly-corrupt disk state:** `try: SomeEnum(s) except ValueError:` +
  a documented default (Problem `state.txt` → `ENABLED` with a warning; a
  malformed schedule `result.txt` → `UNKNOWN` with a warning; a garbage
  commentary `review` → `NEUTRAL`).

The shared `WireEnum` base gives each enum `.value`-based `__str__` (readable
`print`/f-strings), `from_wire`, and `wire_values` (for CLI help/error text).
Crucially these are **plain `Enum`, not a `str` mixin**: a member does *not*
silently serialize.  `json.dumps(member)` raises `TypeError`, and you cannot
`+`-concatenate a member with a `str` — both are *features*, forcing every
boundary to spell out `.value` so a wire format can't drift by accident.  That is
also why the disk/CLI wire strings that stay literals are exactly the *inputs* we
have not yet turned into members (an argparse `default=`, the `"neutral"` fallback
for an absent JSON field): translation happens the instant a value becomes an
in-memory concept.

(Not every fixed token is an enum: the `<RunGenMain>`/`<Lib>` problem-argv
placeholders are named string constants, and pool tags are free-form user
strings.  The enums are for the closed vocabularies the model branches on.)


# Tool Internal Design

**Codebase map** (`dendritic_hl/dendritic_hl_lib/`):

* `main.py` — argparse. `_build_parser()` builds the subcommands (each takes
  `-C`/`-s`); `COMMAND_HELP` (name→one-liner) drives `help`; `_DISPATCH`
  (name→`cmd_*`) routes. `main()` calls `safety.arm()`, acquires the shared
  machine lock, intercepts `exec`/`exec_exclusive`, dispatches, and turns
  `DhHlError` into a stderr message + exit 1.
* `errors.py` — `DhHlError` (user-facing; exit 1, triggers rollback) and
  `HalideBuildError` (subclass; build-environment problems).
* `ids.py` — pure ID/timestamp/hash helpers for schedule, idea, and session IDs
  (`make_*_id`/`looks_like_*_id`/…, plus `sanitize_component` for session
  user/host).  See the `looks_like_*_id` naming note (Enum Policy is separate;
  these are syntactic *shape* checks, deliberately not `is_*`).
* `enums.py` — the fixed-vocabulary `Enum` types (`Result`, `Review`,
  `ProblemState`, `SideLink`, `CostVerdict`, `IdeaStateKind`) and their shared
  `WireEnum` base (see Enum Policy).
* `safety.py` — the rollback/overwrite/commit registry (see File Rollback).
* `locks.py` — the machine directory, the flock lock hierarchy, and the lock-free
  session-handle store (see Lock Hierarchy, Session Handles).
* `catalog.py` — the in-memory model (conceptual description below). `_UNLOADED`
  sentinel; the `EXPECTED_PROFILER_VERSION` constant (the profiler JSON schema
  version the cost tools understand); schedule sub-objects `Commentary`,
  `Benchmark` (with `wall_time_min` / `profiler_version` cost accessors),
  `WarningToggle`; the top-level `BenchmarkSet`; nodes `ScheduleNode` (with generator parameters +
  the `Result` enum / `best_result`), `IdeaNode`, `SessionNode` (prompt, multiple
  seed ideas, default anchor, `outputs.json`); the `CurrentIdeaState` parser; the
  parameter helpers (`DEFAULT_PARAMETERS`/`dump_parameters`/`validate_parameters`/
  `load_parameters_text`); and the top-level `Catalog` (lazy
  `schedules`/`ideas`/`sessions`/`benchmark_sets` dicts, derived child-edge
  linking, dirty set + `flush()`, `mint_timestamped_name`/`mint_session_id`,
  `create_schedule`/`create_idea`/`create_session`/`create_benchmark_set`, the
  edge helpers, and the `session_is_closed`/`session_is_terminus` predicates).
  `Catalog.__init__` enforces the Catalog lock invariant (see Lock Hierarchy).
  Short-ID resolution/formatting are free functions exposed via `Catalog.resolve_*`
  / `format_*` (benchmark sets resolve by exact full ID only).
* `context.py` — `resolve_target` maps `-C`/`-s` (handle or full ID) to
  `(catalog_dir, session_id)`; `Context.for_catalog` / `for_session` acquire the
  locks then wrap a `Catalog`; `SessionWorkspace` is the (catalog-free-readable)
  per-session private workspace owning `generator.cpp`, `generator_parameters.json`,
  `current_idea_state.txt`, and `bin/`, plus the lazy-load-once + flush state
  objects `PrivateIdeaList` / `PrivateBenchmarkSetList` (`_PrivateMapState`) and
  `CurrentAnchor` (see "Private-workspace state objects"); `_benchmark_set_cache`
  builds a benchmark set's cached cost stats; `finish()` = `catalog.flush()` +
  `safety.commit()`; `read_text_or_stdin` handles `-` and turns a missing file
  into a clean `DhHlError`.  `resolve_schedule_arg` resolves an optional
  `[schedule ID]`: an explicit ID hits the catalog directly (no session needed,
  so a `-C`-only tool works without `-s`), while the omitted default is the
  session workspace's unambiguous schedule and therefore needs `-s`.  That
  missing-session case is caught in `resolve_schedule_arg` itself with an
  argument-specific message ("-s required to resolve the default schedule node
  argument ...") rather than letting the generic `self.workspace` "need -s" error
  surface — the generic message misled a reader into thinking the tool required
  `-s` unconditionally.
* `tools.py` — every non-build `cmd_*` (catalog/idea/session queries + session
  lifecycle, the JSON cost query tools, the private-benchmark-set tools) plus
  shared print/JSON helpers and the profiler-stats reachability walk.
* `build.py` — `cmd_init_build` / `cmd_build` (see the Build Tool in idea.md),
  with the toolchain steps behind the monkeypatch seams `_write_ninja`,
  `_ninja_build`, `_discover_generator_name`, `_emit`, `_link`, `_run_benchmark`
  (see Tests).
* `cost.py` — the cost model core behind `json_ranking_cost` / `json_compare_cost`
  and the `list_private_ideas` frontier (see "Cost Model Core"): `CostData`
  (batched `wall_time_min` samples from the private-benchmark-set caches, ranking
  ±anchor, the 2-way `compare`), the paired-difference bootstrap primitives
  (`paired_diff_ci`/`compare_verdict`), and `compatible_sets` — the profiler-version
  gate that drops + stderr-warns on mismatched sets.
* `profiler_stats.py` — the pure `aggregate` for `json_profiler_stats`: per-func
  and pipeline-global statistics (incl. the derived `active_threads` / `*_per_run`
  / `time_ratio` specials) summarised across benchmarks into `[p25, median, p75]`.

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
* A `Dict[str, BenchmarkSet]`: benchmark set objects by full ID

Each object
* is accessed with getters and setters
* has initially empty state, and is lazily initialized from disk when needed by getters
* is dirtied when modified, or upon creation if it's not loaded from disk;
  do this in each setter and non-load-from-disk `__init__` path;
  DON'T ever expect outside code to dirty an object manually!
* has `flush` callbacks that uses the `safety` module to write changes to disk.
* may own lazily-created sub-objects corresponding to some piece of conceptual state;
  for example, a schedule node object owns commentary sub-objects.

**Setters dirty *unconditionally* — a cautionary tale.**  A setter must always
mark dirty; do NOT add a `if self.x == value: return` "skip if unchanged" guard.

* Two setters (`Problem.set_state`/`set_short_name`) once had that guard; every
  other setter did not.  The inconsistency alone was a trap for readers.
* The guard compares against the value the getter *returns*, which for a
  malformed on-disk file is the **lenient default** (a corrupt `state.txt`
  resolves to `enabled`).  So `set_state(enabled)` saw "already enabled" and
  no-op'd — refusing to heal the garbage, and re-emitting the warning every run.
* It bought nothing: the avoided write is a deferred, tiny, already-locked
  overwrite, and git diffs on content so an identical rewrite is invisible.
* Net: a real correctness bug (unhealable corruption) traded for a non-benefit.
  Lazy loading is good; "lazy writing" via value comparison is not.
* Human Note 2026-08-10, David Zhao Akeley:
  if a circumstance shows up where this rule should be revisited
  (optimization is actually significant, and not easy to implement otherwise)
  ask for human judgment on whether this rule should be waived.

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
safety/rollback, and the `init_build`/`build` orchestration with the subprocess
steps stubbed). The genuinely end-to-end tests are marked `halide` (registered in
`pytest.ini`) and auto-skip unless the local Halide build and `ninja` are
present (the build location is derived from the checkout — see "Halide path in
tests" below): `test_halide.py` and `test_params_e2e.py` (in-process via `run_tool`),
and `test_build_cli_halide.py` (real `./dh_hl` subprocess via `run_cli`, using
the `tests/hist_params.cpp` generator to check profiler-stat attribution,
generator-output ordering, failed-generator handling, and the cost tools over
real profiler numbers — `json_ranking_cost` picking the faster parameters
object as representative, `json_profiler_stats` aggregating real per-func
samples, and `json_compare_cost` calling a serial-vs-parallel regression).

**Halide path in tests.** `build` reads the Halide directory from session
private-workspace state (`set_halide_path`), never a hard-wired location — so the
tests must supply one, and they derive it from the checkout rather than any
`~/Halide` literal. `conftest.HALIDE_DIR` is the parent of the harness package
dir (the Halide checkout this harness lives inside) and `HALIDE_BUILD_DIR` is its
`build/` tree, which the `halide`-marked skip guards test for. The
`make_catalog_session` helper sets `HALIDE_DIR` on every session it creates
(mirroring a user running `set_halide_path` right after `new_catalog`, which
itself leaves the path unset); the `run_cli` bootstraps do the same through the
real tool. `tests/test_build_fake.py::test_ninja_has_no_hardwired_halide_path`
independently guards that no `/halide/` path leaks into a generated ninja file
when the session's Halide path contains none.

**Test-only hook in shipped code.** `safety.new_file` honors a
`DENDRITIC_HL_TEST_FAIL_AFTER=<n>` environment variable that raises after the n-th new
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
  covered only by the opt-in `halide`-marked tests (`test_halide.py`,
  `test_params_e2e.py`, `test_build_cli_halide.py`).

So the two tiers are complementary: fake-build pins the orchestration fast and
always; the Halide tests verify the real toolchain integration when present.
Because the fake profiler returns identical dummy stats for every binary, the
*attribution* of profiler stats to the right (schedule, parameters) is checked
only by the Halide tier (the parallel-vs-serial perf and benchmark-set-cell
tests in `test_build_cli_halide.py`).

**Cost-tool testing (deterministic, Halide-free).** The cost model is tested
against *known numbers* without ever running the profiler: `conftest`'s
`add_synthetic_benchmark_set` (with `make_profiler_obj`) fabricates real
`Benchmark` + `BenchmarkSet` objects with hand-chosen `wall_time_min` values (and
optional `funcs` for the profiler-stats tests), so a test knows the exact cost of
every schedule.  This works precisely because the paired-difference bootstrap is
**fixed-seed deterministic** (see "Cost Model Core"): identical samples always
yield the same CI and thus the same improvement/regression/unknown verdict, so a
test can assert an exact verdict.  `test_cost.py` (the `cost.py` core),
`test_cost_tools.py` (`json_ranking_cost`/`json_compare_cost` wiring),
`test_profiler_stats.py` (`profiler_stats.aggregate` + the tool), and
`test_list_private_ideas.py` (the frontier — parsed into per-idea blocks so
cost/pool/obsoleted-by are checked *per idea*, not by loose substring) all rely
on it; `test_private_benchmark_sets.py` also guards the lazy-load-once objects
against the looped-mutation regression.  The Halide tier then re-checks the same
tools against genuinely noisy real timings (above), where only *robust* facts
(the parallel variant is much faster) can be asserted.

**Structural "garbage value" cost tests (pairing / multi-set / representative).**
Bugs that return a plausible-but-WRONG number rather than crashing are the worst
(they send agents in bad directions), so these pin behaviours that near-constant
data would hide.  In `test_cost.py`: `test_pairing_beats_cross_batch_variance`
(large cross-batch variance with a consistent per-batch offset -> the paired
verdict is confidently `improvement`/`regression` where an unpaired marginal CI
would read `unknown`); `test_batches_accumulate_across_sets_without_cross_set_pairing`
(the `(set_id, batch)` key accumulates to `batch_count == 6` across two sets
instead of colliding to 3); and `test_representative_recomputed_over_shared_batches`
plus `test_representative_tie_breaks_to_lower_index` (the representative params
index is recomputed per method over the relevant batch subset, ties to the lower
index).  In `test_cost_tools.py`: `test_ranking_cost_mixed_null_keeps_ordinal_slot`
pins `[cost, null]` for a 2-param schedule with only index 0 profiled (an
unbenchmarked index is `null`, never `0` = "infinitely fast").  Each was
mutation-checked against the specific bug it names.  Already-covered and
deliberately NOT re-added: anchor ratio direction
(`test_ranking_with_anchor_is_ratio`), representative<->cost consistency
(`test_representative_picks_lowest_median_param`), and the profiler-stats
percentile ordering + `time_ratio` denominator
(`test_profiler_stats.py::test_percentiles_and_special_values`).

*Fixture mechanism (decided):* these use `add_synthetic_benchmark_set` unchanged
-- one call per benchmark set (a batch is a position in a cell's sample list; a
second set is a second call, merged with `private.update`), which already
expresses every batch / set / params / problem axis a cost test needs.  No
fake-`build --profile` seam or backdoor insert-tool was warranted: the
model-level helper keeps each test's inputs visible at its call site, and the
multi-set pattern (`test_compare_no_shared_batches`) predates these tests.

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

*Listening in on lock behavior AND build commands.* `locks._trace(event)` appends
to `locks._trace_sink` when a test sets it to a list (a no-op otherwise). Two
kinds of event share this one ordered stream:

* Lock events — each `acquire_*` records `("machine","shared")`,
  `("session","exclusive")`, `("machine","exclusive")`, `("catalog","exclusive")`
  in order, so a test can assert the exact per-command lock sequence: e.g. that
  `build --profile` upgrades the machine lock to exclusive before taking the
  catalog lock and a non-profiling `build` does not, or that a read-only tool
  skips the session lock.
* Build command events — `build.py` records the toolchain steps it issues as
  `("build", <phase>, *detail)` (`cpp_compile`/`emit`/`link`/`batch`/`profile`)
  via `build._trace_build`, deliberately onto the SAME sink (a "piggyback").
  Sharing one stream is the point: `test_halide.py::test_build_command_trace_and_shuffle`
  runs the REAL toolchain in-process and asserts both the command sequence AND
  its ordering relative to the lock acquisitions (all compilation before the
  machine-exclusive upgrade + catalog lock; all profiling after), plus that the
  per-batch profiling order is shuffled. Tests that only want the lock sequence
  filter build events out (`[e for e in sink if e[0] != "build"]`).

This is white-box coverage the subprocess tier cannot observe; real cross-process
mutual exclusion is instead covered by the subprocess timing test (`test_locks.py`).
(`run_tool` resets the sink per call, so `locks._trace_sink` reflects the most
recent command.)

*Ordering of `dh_hl:` lines vs child output.* `build._run_streamed` flushes our
own stdout/stderr before launching each child, so the harness's `dh_hl:` banners
are ordered before the child's output in a piped/captured stream (our Python
stdout is block-buffered when piped). This matters for printf-debugging a
generator and is asserted by `test_build_cli_halide.py` (generator prints land
between the begin/end generator banners).
