# Human-added Tasks

Grouped into sections; sections are meant to be done in stated order, with tasks within a section not necessarily ordered.

## `specialize` Doc Edits

[ ] Briefly mention that `f.specialize(...)` returns an existing function handle if given a duplicate `Expr`, and that this is out-of-scope for `micro_halide`.

[ ] Rephrase the `select` paragraph as a separate explicit "known limitation" sub-section, which introduces the poorly-tested workaround.
    Basically, roll back to something similar to the "impossible" language, except an ",unless ..." calling forward to the weird sub-section.
    Declare simplifying `select` as out-of-scope for `micro_halide`.

[ ] Update "objects and their conceptual state" with the extra specialize state.

[ ] Update `in`/`clone_in` section with brief interaction with `specialize`.
    Also, what happens if the func parameter is a Func handle from `f.specialize(...)`?
    Is it exactly equivalent to passing `f` itself?

[ ] If not done, write some examples where the specialized function is deep in the pipeline, and not the top-level function.
    Inform me of the names of the new or existing examples.

[ ] Clarify if `Identical branches merge` is intended to be out-of-scope for `micro_halide`.
    If so, I agree with this decision.
