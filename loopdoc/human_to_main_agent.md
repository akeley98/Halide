# Human-added Tasks

New adversarial cases.
Mainly to test the issue that although the algorithm is pure-functional-ish, the scheduling metaprogramming is definitely not.
There's a lot of issues of preserving mutated scheduling state, and reasoning about when different handles have aliased state or not.

Use the parameterizable .hpp + simple .cpp example structure to create example families.
To eliminate doubt, the README.md has been edited to explicitly authorize agents to write such examples.

## Earlier Tasks

[ ] `rfactor` before `specialize`.
    Then schedule the `rfactor` output.
    This should propagate to both branches of the `specialize`.
    Make this a family of 2 examples: optionally, `tile` the func before `rfactor`, and `rfactor` only the inner loop.
    `rfactor_then_specialize_tiled` seems to need a collapses annotation.

[ ] `specialize` then `rfactor` on each branch.
    Give non-trivially different schedules to both `rfactor` output functions.
    Also family of 2 examples: `tile` or not `tile`.

[ ] Mix of the above two with non-trivial `specialize` tree structure.
    (Since there's a tree of `specialize`, this means it's possible to have `rfactor` before some `specialize` branches and after other `specialize` branches).
    `tile` at some point in all this.

[ ] Write a family of `clone_in` + `specialize` examples:
    Choice A:
        A0. `specialize` followed by `clone_in`
        A1. `clone_in` followed by `specialize` of the original
        A2. `clone_in` followed by `specialize` of the clone
    The `clone_in` will use the form where it's passed a list of two functions `g` and `h`.
    `g` is pure and `h` uses the cloned function in an update stage.
    Choice B:
        B0. `g` and `h` are fused with `compute_with`
        B1. `g` and `h` are both producers of some `f`, and the cloned function is `compute_at(f, ...)`
        B2. Same, but additionally `h` has been `rfactor`'d so the `rfactor`-output function is what consumes the cloned function. Based on your finding, this will be a negative case due to `h` no longer directly consuming.
        B3. The above example corrected to be `clone_in({g, h_intm})` instead of `clone_in({g, h})`.
          Correct me if I'm wrong (so don't write the new test if I'm wrong), but is there still a contradiction of the scheduling order if `h` is rfactor'd FIRST before any of the other stuff mentioned above?
        B4. do something else that will cause the schedule to be illegal (negative example)
    Also make sure the original func (`this` of `clone_in`) is non-trivially used somehow.
    Test all non-impossible combinations of choice A and choice B.

## Follow up Tasks

[ ] First, I'm not convinced that the root cause of the failing B3 tests is that intractable.
    Look at `build/examples/clone_spec_a0_b3_micro_halide_HUMAN_REORDERED.log`.
    I exchanged the realization order of `f` and `p` from the `micro_halide` nest.
    Now the diff is small, and appears to be a likely `micro_halide_collapses` issue.
    So the root cause seems to be realization order again.

[ ] Check if the above test failure is fixable using only the current `loopdoc.md` information.
    It seems not clear to me why this happened since `f` and `p` are clearly sortable alphabetically,
    so `micro_halide` should have put `f` before `p`.

[ ] Key to reasoning about legal/illegal schedules in understanding producer/consumer relations.
    There's two notions of "consume".
    One is by checking which functions appear on the LHS/RHS of all stages.
    But for `compute_at`, what really matters is whether the function READS a realized consumer.
    This seems to be vaguely mentioned in passing for the "when is `compute_at` legal" section:
>    **`g` is not a consumer.** No stage of `g` reads `f` (directly or through Funcs
>      inlined into it), so nothing in `g` needs `f` —
>      [examples/neg_compute_at_nonconsumer.cpp](examples/neg_compute_at_nonconsumer.cpp).
    Is this defined more carefully elsewhere?
    The process seems to be recursive, basically expanding the "producers" set by recursively replacing pure inline functions in the set with said function's producers.

[ ] Investigate the `compute_at_inline_dependence_update_inline.cpp` example, which may or may not be related to the above issue.

[ ] Is the B2 case derivable from existing `loopdoc.md` rules, or is it actually a new rule specific to `in`/`clone_in`?
    I suspect it is not currently covered in `loopdoc.md`, or at least wasn't implemented by `micro_halide`.
    See the new `clone_in_unused.cpp` example.
    Also, why doesn't the transitivity rule below make `clone_in(h)` legal from transitivity via `h_intm`?
>     * As with Func::in(), clone_in() acts transitively: any Func in 'f'/'fs'
>     * that does not directly call this Func is replaced by the set of direct
>     * callers reachable from it along paths that lead to this Func. Only
>     * this Func is cloned; the intermediate Funcs along the path are not.
