# Human-added Tasks

Are the following reasonable advice?

## New discussion 

[ ] Proposed advice: avoid the transitivity feature as much as possible in `in`​ and `clone_in`​. Because the search stops immediately at direct consumers, if all parameter functions consume `*this`​ directly, then you don't have to worry about what the transitivity does or does not do.
    Claude found the feature is needed when you have to modify an anonymous function, like from `sum`.

[ ] Proposed advice: `f.in(g)` and `f.clone_in(g)` work "as expected" if `g` is itself a clone that consumes `f` directly (i.e. this specific case is NOT blind to clones, only the transitive search of other functions that `g` consumes that would have happened if it were not the case that `g` consumes `f` directly)

[ ] Proposed advice: To work around `f.clone_in(...)` being a failure, if `f` is PURE, for most purposes it will suffice to replace `f.clone_in` with `f.in` (a wrapper) and leave `f` as its default schedule (`compute_inline`).
    You can have multiple `f.in` wrappers each `compute_at` different places in the pipeline.

[ ] ... except this workaround won't work so well if you want each `f.in` to use a different clone/wrapper of something `f` consumes (say `f_input`).
    Because the `f.in` doesn't consume anything other than `f`, and that common `f` can't sometimes use one clone/wrapper of `f_input` and sometimes another.

[ ] ... the `f.clone_in(...)` to `f.in(...)` workaround doesn't work as well if `f` is impure.
    Because in this case, the original `f` will have the default, highly inefficient default schedule,
    and if you try to schedule it with `f.compute_at(f.in(...), ...)`, this will break other `f.in` wrappers.

[ ] There seems to be no *universally-applicable* workaround for `f.clone_in` not working twice, except modifying the algorithm.
