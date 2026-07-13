# Human-added Tasks

[ ] Do another pass to more fully eliminate `micro_halide` references
    that don't reference `micro_halide` by name. Move this information
    to `micro_halide.md` even if this splits information up.  
    There are many cases where references to `micro_halide` are laundered
    by replacing to "this document", desipte that the information would make
    no sense without knowing about `micro_halide`, such as:
> Halide prints each Func's name, but this document identifies Funcs *positionally*
> (`F0`, `F1`, … in order of first appearance) rather than by exact name, so you only need
> to get the *structure* and the *number of distinct Funcs* right, not reproduce
> Halide's exact names. [This is obviously prompting the micro-agent specifically]
    Unfortunately this means section 10 will be empty, I will tolerate
    this as a doc wart for now.

[ ] Move "realization order in detail" also to its own detail document.
    Reasonable to introduce the realization order as "topological sort"
    of producer/consumer relations and then explain that
    tie-breaking, fused groups, and other complicated cases are in the detail doc.
