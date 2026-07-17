# Session Nodes

Main contents moved to [idea.md](idea.md) and [impl.md](impl.md).

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


# Agent Workflow

* Main agent needs to create a new catalog if there isn't already one.
  Then use its session handle for all further harness usage.

* Otherwise, the main agent needs to query for a terminus.
  There should be exactly one, either continue it if open or start a successor session if closed.
  Use the existing or new open session as the current session.

* If they spawn sub-agents, agents need to create a new sub-session and instruct
  the sub-agent to use the new session's handle for all harness usage.
