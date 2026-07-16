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

* Each agent needs to have an "active session", which controls which session node they're associated with.
  Most `dh_hl` commands will require a current session, set by environment variable.


# Lock Hierarchy

Higher-up locks must not be acquired if lower locks are already held.

* Session Lock: exclusive lock per session node on disk;
  protects only the private session workspace.
  This is only to help detect SNAFUs where two agents somehow got the same session node.
  The agent is allowed to work in the private session workspace without this lock.

* Machine Lock: global to the machine in `~/.cache` (well global-ish).
  Acquired exclusively for profiling, concurrently for all other uses.

* Catalog Lock: acquire exclusive access to the catalog directory,
  other than private session workspaces.
  This needs to not be held during the "build C++" phase,
  which could be done with the private workspace only
  and would be a serial bottleneck if locked.

Need to figure out how to make the locking work "by design" in an
overarching abstraction and not require this manually.
Maybe acquire session (if applicable) and concurrent machine locks on startup,
and require the catalog lock to be held before constructing the Catalog object?

The profiling command has a build C++ step and a benchmark+record-results step.
This requires a transition from "concurrent machine lock, no catalog lock" state
to "exclusive machine lock, exclusive catalog lock" state.

This transition is why the session lock is at the top.

Figuring out how to test this is an open question.


# Session Node State

* A reference to a starting idea node.
  For sub-agents, the proposal of the idea is the prompt.

* An optional reference to an output schedule node.
  Sub-agents should use the commentary to give a report.

* An "is delisted" flag.

* A depth value. Top-level sessions have 0 depth.

* Private workspace state

* An optional parent schedule node.
  Only two types of edges are allowed:
  - Parent node to child node of 1 greater depth.
    Nodes reachable via only such edges are sub-sessions.
  - Parent node to child node, both of 0 depth (top-level sessions).
    Nodes reachable via only such edges are successor sessions.


# Session Node Concepts

* A session is "self-closed" if it has an output schedule node or is delisted.
  A session is "closed" if it's self-closed or a sub-session of a self-closed session.
  It is "open" otherwise.

* A session is a "terminus" if it is top-level and has no successor sessions and is not delisted.
  Generally there should be only one terminus, and further progress should start from there.

* The current session is required for almost all `dh_hl` tools,
  given by the `DENDRITIC_HL_SESSION` environment variable.
  Sub-agents will be instructed which session variable to export.

* "Current idea state" is now per-session state, and not tracked in git.

* "Workspace C++ file" (a.k.a. workspace schedule) and the compiled `bin` outputs are also per-session state,
  not tracked in git.


# New Commands

Not fully specified yet, just for discussion.

    dh_hl new_sub_session
    dh_hl new_successor_session

Create a new sub-session or successor session, parented to the current session.
This is seeded with a new idea node, whose proposal is the prompt and canonical schedule
is just a copy of the idea node's parent schedule.

    dh_hl new_catalog

Replaces the default init behavior of `new_root`.
Creates a new catalog directory with the bare minimum state to get started.
Two schedule nodes holding the same initial schedule,
connected by an idea node with the inital proposal/prompt,
and with one session node (terminus) seeded with that idea node.

    dh_hl list_open_sessions
    dh_hl list_termini

List all open session nodes or all termini.

    dh_hl copy_schedule_node
    dh_hl copy_terminus_schedule
    dh_hl copy_workspace_schedule

Copy a C++ schedule to an output file.
Get the schedule from the given schedule node,
the output schedule node of the unique terminus,
or the current workspace.

    dh_hl workspace

Get the filename of the workspace C++ file.

    dh_hl close_session

Close the current session (with an output schedule node)

    dh_hl delist_session

Delist the session

    dh_hl exec
    dh_hl exec_exclusive

Execute a CLI command with the machine lock held, either concurrent or exclusive


# Agent Workflow

* Main agent needs to create a new catalog if there isn't already one.
  Then export the single new session as the current session.

* Otherwise, the main agent needs to query for a terminus.
  There should be exactly one, either continue it if open or start a successor session if closed.
  Export the existing or new open session as the current session.

* If they spawn sub-agents, agents need to create a new sub-session and set it up so the
  sub-agent's environment has the new sub-session as the current session.
  Please advise if this is actually possible with the tools available.
