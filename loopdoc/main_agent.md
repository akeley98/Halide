# Main Agent Instructions

The purpose of the main agent is to write the loop documentation and the accompanying Halide examples.
The main agent will mostly NOT implement `micro_halide`.
Instead, the main agent delegates this to sub-agents that are reading the main agent's documentation.


## Flow

* Look inside `progress.txt`.
  If there is a WIP milestone, keep working on it.
  Otherwise, work on the first incomplete milestone.
  The milestone is a *rough guide* of which Halide features to document next.
  Also read the `DISCOVERED DOC GAPS` section at the bottom of `progress.txt`:
  these are concrete questions that `loopdoc.md` previously failed to answer.
  Open gaps relevant to the current milestone are first-class work -- prefer closing
  them over adding new coverage. When you fix one, mark it `[fixed]` rather than
  deleting it, so the record of what was once unclear survives.
  Since every feature interacts with every other, use your judgment as-needed to skip documenting some functionality if it relies too much on not-yet-documented topics, or partially document features ahead of their listed order when it makes the explanation more coherent.
  Update NOTES in `progress.txt` as appropriate to coordinate reordering work with other milestones.

* Start analyzing the features by
    * Reading `../src/Func.h` comments documenting the scheduling operation
    * Reading relevant Halide tutorials (`../tutorial`) or example apps (`../apps`)
    * Writing example Halide programs in `./examples` to test for yourself how Halide works
    * Reading the Halide compiler's source code itself.

  Please write a large number of examples comprehensively analyzing the interaction between the current milestone's features and previously documented features.
  When the time is right, consider:
    * Multiple functions `b1`, `b2`, `b3`... relying on a common producer `a`, and possibly another function `c` consuming `b1`, `b2`, `b3`, ...; and consider `in()`, `clone_in()`, and the "transitivity" documented for these scheduling operators
    * Complicated patterns involving update functions

  Try to write both examples that execute successfully and those that fail.
  The failed "illegal schedules" are a huge headache for Halide users and the conditions that cause them need to be documented better.
  The primary cause for illegal schedules is a producer being run somewhere in the loop nest such that the consumer cannot actually read those values at the right time, but read the source for other possible failures.

* Loop:
    * Analyze the Halide features
    * Update `loopdoc.md` to describe those features. The `loopdoc.md` must document
        * The conceptual "state" of Halide API objects
        * How the state is modified by scheduling operations
        * A description of the logic involved in translating this scheduled state into a loop nest; here, the documented logic is at the level of detail needed for a user to understand how the compiler works conceptually.
      IMPORTANT: the milestones are not an outline of how to structure the `loopdoc.md`!!!
      Each milestone involves *holistically* editing the entire documentation into a coherent whole, not just appending more text at the end.
    * Write/update C++ examples in `examples/`, and cite those examples in the `loopdoc.md` at appropriate points to help illustrate the documentation.
    * The Hard Part: Back-up your claims in the `loopdoc.md` by explaining, with a new or modified file in `src_doc`, how the Halide compiler is *implementing* the documented behavior.
      Unlike the main `loopdoc.md` file, this is at a level of detail suitable to help humans maintain the Halide compiler.
      Insert citations to the `src_doc` files in `loopdoc.md` to back up the claimed behavior.
      As needed, insert `debug(1) << "text";` logging into the Halide compiler itself, and include transcripts of the debug log to illustrate the compiler's internal logic.
    * Make a git commit
    * If this is not the bootstrap milestone, spawn micro-agents to edit `micro_halide` based on the documentation you've written.
      You may wish to leave comments using `<!-- -->` syntax in `loopdoc.md` highlighting what changed to guide the micro-agent.
    * If this is the bootstrap milestone, edit `micro_halide` yourself to make the tests (`sh test.sh`) pass.
    * Make a git commit, with commentary on whether the tests are passing or failing.
    * If the tests passed:
        * Check that the micro-agent didn't do something stupid like delete test cases; flag for human review if so.
        * Mark as [fixed] the discovered doc gaps that seem to have been resolved.
        * Exit the loop: milestone complete.
    * If the tests don't pass:
        * Try to understand what difficulties the micro-agent ran into. They should have
          left comments in `loopdoc.md` and appended one-line entries to the
          `DISCOVERED DOC GAPS` section of `progress.txt`.
        * Treat each open gap as a precise statement of what the documentation failed to
          convey. Improve `loopdoc.md` (and `src_doc`/examples) to answer it.
          A failing test is a *useful result*: the deliverable of this
          campaign is documentation that closes these gaps, not merely a green test run.


## Rules

* DO NOT modify the harness.
  If you suspect a bug in the harness, flag it for human review.

* DO NOT spawn a micro-agent if the C++ compilation step failed.
  Implement stub functions in `micro_halide` as needed to make examples compile (but not necessarily execute successfully).

* Avoid implementing non-trivial logic in `micro_halide`.
  It's a judgment call what "non-trivial" is: decide by considering the purpose of `micro_halide` is to test the micro-agent's comprehension of the documentation you are writing.
  Since only you (and not the micro-agent) are allowed to reference the Halide source code, you should probably implement drop-in replacements for classes and functions that are patterned after the real Halide, but have placeholder implementations (`throw std::runtime_error("TODO xyz")`).
  Certain milestones will require implementing a lot of auxilliary types like `Rdom`, `Rvar`, `Stage` where most of the work is just imitating the Halide API; in this case it's appropriate to do more work.

  Exception: bootstrap milestone is entirely exempt from this rule.

* Insert `micro_halide_collapses` as appropriate; however, since `micro_halide` is not fully implemented, and the real Halide ignores this, you will have to reason (by reading the Halide loop nest) where to inject `micro_halide_collapses` without being able to test it.

* The human is not experienced in agentic coding and indeed has not a very clear picture of what he's doing.
  You may stop to give suggestions if the harness or milestone list or overall way of doing things seem counterproductive.


## Halide Source Code References

* `../apps`: example apps

* `../tutorial`: tutorials

* `../src/Func.h`: header for most scheduling functions

* `../src/ScheduleFunctions.cpp`: primary logic for making the loop nest, using `InjectFunctionRealization` within `schedule_functions`.

* `../src/IR.h`: internal representation of the loop nest.

* `../src/PrintLoopNest.cpp`: implementation of `print_loop_nest`, which calls `schedule_functions`.

