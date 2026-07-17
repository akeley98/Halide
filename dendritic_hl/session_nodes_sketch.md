# Session Nodes

Major curveball: talking to my professor,
it seems having agents work in parallel is actually something they're very interested in.
My original simplification for parallelism was
"assume no concurrent catalog usage, and give each agent a catalog copy, merge together with git".
This ignores the complication of how to enforce exclusive machine access when profiling,
as well as annoyances from merging the workspace and current idea state.
Also part of the goal of the harness is to be able to store a full "history" of the LLM scheduling process,
for research purposes to review later.
So I'm considering making the parallelism and "session history" a first-class concept in the harness.


# Top-Level Goals

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


# Lock Hierarchy

* Session Lock: exclusive lock per session node on disk;
  protects only the private session workspace.
  Unlike the other locks, this lock isn't necessary with correct tool usage and just exists
  to give a prominent warning of "concurrent session use detected".
  The agent is allowed to work in the private session workspace without this lock.

* Machine Lock: meant to be global to the machine.
  Acquired exclusively for profiling, concurrently for all other uses.

* Catalog Lock: acquire exclusive access to the catalog directory,
  other than private session workspaces.
  This needs to not be held during the "build C++" phase,
  which could be done with the private workspace only
  and would be a serial bottleneck if locked.

Each tool may do a subset of these four actions, done strictly in top-to-bottom order:

* Acquire exclusive session lock; non-blocking and exit-with-failure if not acquired

* Acquire concurrent machine lock (blocking)

* "Upgrade" machine lock to exclusive (blocking; not guaranteed atomic by OS, like release and acquire)

* Acquire exclusive catalog lock (blocking)

For now, the assumption is still that tool calls are short-lived, so we just rely on the OS
to release the locks upon process exit, which happens strictly after the `atexit` safety handlers.
Unlocking works even if the process is killed or segfaults, unlike the `atexit` file deleter
(which is an existing limitation that also should be documented).

I want locking to work "by design" in an overarching abstraction,
and not require most code to worry about whether the lock was held.
Maybe,

* The existing safety system is the bottom-level arbiter for keeping the rest of the code behaving well.
  It exposes "lock" functions, and asserts no locks acquired out-of-order.

* Session lock acquired on startup as soon as arg parsing is finished, if a session was given.

* Concurrent machine lock acquired after that (whether or not the session lock is held)
  (flaw: Python startup and tool arg parsing steals a bit of time from running profilers)

* `Catalog` either auto-locks or checks the catalog lock state from the safety system.
  Can assume the catalog lock is held if you already have this object or a catalog sub-object.

The `build` and `profile` tools require some considerable adjustment to avoid
serializing the expensive C++ build step.

* The C++ build step runs with only the session lock and concurrent machine lock held.
  It requires purely the session workspace `bin` directory.
  Don't actually determine the edited schedule node yet.
  (Hence some invalid commands will not be diagnosed until after the C++ build).

* After the C++ build (success or failure), the harness proceeds to the next step depending on the tool.

* `build` tool OR failed C++ build: acquire the catalog lock,
  run the Halide generator (if it built),
  and record the result to the edited schedule node.

* `profile` tool AND successful C++ build: upgrade machine lock to exclusive,
  acquire catalog lock,
  and run the current generate/profile/record-results loop.
  It is technically slightly wasteful to lock while running the Halide generator,
  but I think it's acceptable for now.

This machine lock transition is why the session lock is ordered before the machine lock.

Figuring out how to test this is an open question.

Issue: `Context` eagerly creates the catalog,
and a boolean "catalog-needed" flag is not enough for the build/profile behavior.

Issue: can we safely acquire the machine lock the instant after the Python interpreter starts,
because the session lock is non-blocking, and failed session locks are a user bug,
and not something that could lead to livelock in correct harness usage?


# The Machine Directory

The machine lock file and upcoming "session handle" state are stored in the "machine directory".
It's `~/.cache/dendritic_hl/` by default (which is global enough for a single user system).
We will accept the standard `XDG_CACHE_HOME` environment variable to override `~/.cache`

Out-of-scope for now: think about doing profiling remotely through `slurm` or such,
which solves the multi-user problem and no-local-GPU problem.


# Session Node State

* A reference to a starting idea node.
  For sub-agents, the proposal of the idea is the prompt.

* An optional reference to an output schedule node.
  Sub-agents should use the commentary to give a report.

* An "is delisted" flag.

* A depth value. Top-level sessions have 0 depth.

* An optional parent schedule node.
  Only two types of edges are allowed:
  - Parent node to child node of 1 greater depth.
    Nodes reachable via only such edges are sub-sessions.
  - Parent node to child node, both of 0 depth (top-level sessions).
    Nodes reachable via only such edges are successor sessions.

* ID: `{depth}_{timestamp}_{username}@{hostname}`
  (idk, spitballing for now; I just want it to be easy to filter top-level sessions)

* Current idea state, **gitignored**

* Workspace C++ file (a.k.a. workspace schedule), **gitignored**

* `bin` directory (workspace bin directory), **gitignored**

All of the gitignored state is the "private session workspace"


# Session Node Concepts

* A session is "self-closed" if it has an output schedule node or is delisted.
  A session is "closed" if it's self-closed or a sub-session of a self-closed session.
  It is "open" otherwise.

* A session is a "terminus" if it is top-level and has no successor sessions and is not delisted.
  Generally there should be only one terminus, and further progress should start from there.

* The current session is required for almost all `dh_hl` tools.
  Sub agents are to be given a unique session handle for their work.


# Session Handles

Agents need a succinct way to communicate the catalog directory and current session's ID.
This is done by session handles: a shortened, **machine-scoped** alias for that pair.

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

(Edited) Claude-generated pseudocode:

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

The `tmp.` prefix is to emphasize how fragile these handles are.
They will not make any sense on another physical PC.

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

This is quite different from the scheme for short IDs;
in particular, it will never be ambiguous on one machine,
but is entirely divorced from git state (i.e. it's practically
guaranteed that session handles will not mean what is intended
on another machine, perhaps silently meaning something different).

For now, Claude advises sticking with the existing short ID scheme
for ideas and schedules. There is a small risk that an agent may
receieve a short ID, try to use it later, and find it's now ambiguous
due to another concurrent agent's action.
For now, the mitigation is the 6-hash-char minimum rule,
and always giving an explicit ambiguity error instead of guessing.


# New Commands

Not fully specified yet, just for discussion.

    dh_hl new_sub_session
    dh_hl new_successor_session

Create a new sub-session or successor session, parented to the current session.
This is seeded with a new idea node, whose proposal is the prompt and canonical schedule
is just a copy of the idea node's parent schedule.
The new session's workspace C++ file and current idea state are initialized
to the copied schedule and seed idea node.

Automatically allocate and give back a session handle to refer to the new session node.

    dh_hl new_catalog

Replaces the default init behavior of `new_root`.
Creates a new catalog directory with the bare minimum state to get started.
Two schedule nodes holding the same initial schedule,
connected by an idea node with the inital proposal/prompt,
and with one session node (terminus) seeded with that idea node.

Automatically allocate and give back a session handle to refer to the new session node.

    dh_hl list_open_sessions
    dh_hl list_termini

List all open session nodes or all termini of the current catalog.

    dh_hl copy_schedule_node
    dh_hl copy_terminus_schedule
    dh_hl copy_workspace_schedule
    dh_hl copy_session_output

Respectively, get the schedule from

* the given schedule node
* the unique terminus's output schedule
* the current session's workspace
* the current session's output schedule

and copy it to an output file.

    dh_hl workspace_schedule
    dh_hl workspace_bin

Get the filename of the workspace C++ file or bin directory

    dh_hl close_session

Close the current session (with an output schedule node)

    dh_hl delist_session

Delist the current session.
This is mainly intended for human cleanup in case there are multiple termini,
which shouldn't happen with suggested use.

    dh_hl exec
    dh_hl exec_exclusive

Execute a CLI command with the machine lock held, either concurrent or exclusive

    dh_hl idea_full_id      # Any idea ID -> idea full ID; requires catalog
    dh_hl schedule_full_id  # Any schedule ID -> schedule full ID; requires catalog
    dh_hl session_full_id   # Gives current session full ID
    dh_hl idea_short_id     # Any idea ID -> idea short ID; requires catalog
    dh_hl schedule_short_id # Any schedule ID -> schedule short ID; requires catalog
    dh_hl session_handle    # Gives handle for catalog and current session

Goof on my part: it's a footgun to embed short IDs (and now session handles) in
"persistent" places, and I practically force it by handing out short IDs almost
everywhere and not making translation easy for the harness user.


# Agent Workflow

* Main agent needs to create a new catalog if there isn't already one.
  Then use its session handle for all further harness usage.

* Otherwise, the main agent needs to query for a terminus.
  There should be exactly one, either continue it if open or start a successor session if closed.
  Use the existing or new open session as the current session.

* If they spawn sub-agents, agents need to create a new sub-session and instruct
  the sub-agent to use the new session's handle for all harness usage.
