# Human-added Tasks

New adversarial cases.
Mainly to test the issue that although the algorithm is pure-functional-ish, the scheduling metaprogramming is definitely not.
There's a lot of issues of preserving mutated scheduling state, and reasoning about when different handles have aliased state or not.

Use the parameterizable .hpp + simple .cpp example structure to create example families.
To eliminate doubt, the README.md has been edited to explicitly authorize agents to write such examples.


[ ] `rfactor` before `specialize`.
    Then schedule the `rfactor` output.
    This should propagate to both branches of the `specialize`.
    Make this a family of 2 examples: optionally, `tile` the func before `rfactor`, and `rfactor` only the inner loop.
    NEW!!! `rfactor_then_specialize_tiled` seems to need a collapses annotation.

[ ] `specialize` then `rfactor` on each branch.
    Give non-trivially different schedules to both `rfactor` output functions.
    Also family of 2 examples: `tile` or not `tile`.

[ ] Mix of the above two with non-trivial `specialize` tree structure.
    (Since there's a tree of `specialize`, this means it's possible to have `rfactor` before some `specialize` branches and after other `specialize` branches).
    `tile` at some point in all this.

[ ] Write a family of `clone_in` + `specialize` examples:
    Choice A:
        * `specialize` followed by `clone_in`
        * `clone_in` followed by `specialize` of the original
        * `clone_in` followed by `specialize` of the clone
    The `clone_in` will use the form where it's passed a list of two functions `g` and `h`.
    `g` is pure and `h` uses the cloned function in an update stage.
    Choice B:
        * `g` and `h` are fused with `compute_with`
        * `g` and `h` are both producers of some `f`, and the cloned function is `compute_at(f, ...)`
        * Same, but additionally `h` has been `rfactor`'d so the `rfactor`-output function is what consumes the cloned function. Based on your finding, this will be a negative case due to `h` no longer directly consuming.
        * NEW!!! The above example corrected to be `clone_in({g, h_intm})` instead of `clone_in({g, h})`.
          Correct me if I'm wrong (so don't write the new test if I'm wrong), but is there still a contradiction of the scheduling order if `h` is rfactor'd FIRST before any of the other stuff mentioned above?
        * do something else that will cause the schedule to be illegal (negative example)
    Also make sure the original func (`this` of `clone_in`) is non-trivially used somehow.
    Test all non-impossible combinations of choice A and choice B.
