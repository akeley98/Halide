<!--
  FORMAT CONTRACT (dendritic_hl_lib/prompts.py): the "dh_hl prompt" command
  assembles either the main or the sub agent prompt from this file.  Rules:
  * Text outside any fence is COMMON: it goes into both prompts.
  * A fence is an HTML comment whose only word is "main" (or "sub"); it opens a
    region for that audience, closed by a matching "end main" ("end sub") HTML
    comment.  See the fences used in the body below.
  * Fences must not nest, and every opener needs a matching closer.
  * Single word HTML comments are reserved for fences; use multi word comments
    for maintainer notes.  Comments are stripped from the emitted prompt.
-->
# Dendritic Halide Harness: Agents Prompt

<!--

dh_hl prompt:

Task + harness intro
    * Are scheduling Halide
    * Main/sub-agent distinction; orchestrate or flesh out an idea.
    * Use

* Golden Rules

    * No background tasks EXCEPT for launching other agents with their own session
    * Use your session handle always (read-only exception for main agent only)
    * Don't run commands outside the Harness unless really needed

Main agent:
    * Case A: no catalog yet, assign yourself a session
    * Case B: closed terminus
    * Case C: unclear, ask, then run status

Sub-agent
    * Check current session, then run status.
    * Ensure status

Workflow:

Always pass `-s`

dh_hl workspace [problem if there isn't one -- main agent can recover]
This is the file you will edit

dh_hl status needs to be updated 

dh_hl restore

Agent launching rules

Sub-agent: use commentary to give back information.

================================================================================

* idea.md (harness usage)

================================================================================

* loopdoc

Rewrite to be better one day.
Keep the existing detail sections though, probably.

================================================================================

* Andrew's schedule guide; chunk it up.
  Also revise some language.

Part 1
1. The mental model
2. The default schedule is INLINE
3. Directive reference

Part 2
4. The 95% schedule: one outer parallel loop + vectorize stride-1
5. Inline cheap Funcs; schedule only the ones that earn it
6. Exceptions: when to break the 95% shape

Part 3
7. The dev loop
8. The profiler is your primary tool
9. Reading `.stmt` for vectorization shape

Part 4
10. Sliding window for stencils
11. Tiling and storage layout
12. Pyramids
13. Long stencil chains: periodic `compute_root` checkpoints
14. Histograms / scatters
15. compute_with for sibling Funcs (advanced)

Part 5
16. True axis-level recurrences
17. The parallel loop must be OUTERMOST
18. compute_at recompute multipliers
19. Expensive producers inside RDoms: hoist them OUT
20. Common mistakes catalog

Part 6: Reference
21. Pre-flight checklist
22. Worked example
23. When in doubt

-->

This is an instruction for all agents.

<!-- main -->
This is an instruction for a main agent.
<!-- end main -->

<!-- sub -->
This is an instruction for a sub agent.
<!-- end sub -->

This is an instruction for all agents.
