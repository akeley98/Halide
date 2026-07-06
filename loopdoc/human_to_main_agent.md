# Human-added Tasks

Grouped into sections; sections are meant to be done in stated order, with tasks within a section not necessarily ordered.

## `specialize` Doc Edits

[x] Briefly mention that `f.specialize(...)` returns an existing function handle if given a duplicate `Expr`, and that this is out-of-scope for `micro_halide`.
    DONE: loopdoc §15 "Out of scope" first bullet (returns the handle to the existing specialization; distinct conditions used throughout; no Expr-equality bookkeeping in micro_halide).

[x] Rephrase the `select` paragraph as a separate explicit "known limitation" sub-section, which introduces the poorly-tested workaround.
    Basically, roll back to something similar to the "impossible" language, except an ",unless ..." calling forward to the weird sub-section.
    Declare simplifying `select` as out-of-scope for `micro_halide`.
    DONE: §15 "Producers under a specialized consumer" now says it is "impossible, through scheduling ... *Unless* you step outside scheduling entirely" -> new subsection "Known limitation: no per-branch producer scheduling" frames the select workaround as an off-label, unverified, silently-degrading last resort, and declares select-simplification out-of-scope for micro_halide. src_doc/specialize.md has the mechanism.

[x] Update "objects and their conceptual state" with the extra specialize state.
    DONE: §1 Func bullet list gains a per-stage "specializations" entry (ordered list; each a condition + forked schedule copy that may nest; specialize_fail = terminal, no fallback).

[x] Update `in`/`clone_in` section with brief interaction with `specialize`.
    Also, what happens if the func parameter is a Func handle from `f.specialize(...)`?
    Is it exactly equivalent to passing `f` itself?
    DONE: §13 new "Interaction with specialize" subsection. ANSWER to your question: NOT equivalent, and not even allowed. `f.specialize(cond)` returns a `Stage`, and `in`/`clone_in` take a `Func` (`Stage` does not convert to `Func`), so `g.in(f.specialize(cond))` does NOT COMPILE (verified). Wrappers/clones are keyed by consumer Func, never by branch -> one wrapper is read in ALL of the consumer's branches; there is no "wrap only one branch."

[x] If not done, write some examples where the specialized function is deep in the pipeline, and not the top-level function.
    Inform me of the names of the new or existing examples.
    DONE. Example names:
      * specialize_producer_self.cpp (existing) -- the producer `g` (two levels below the output `out`; chain g -> f -> out) is the specialized Func.
      * specialize_midchain.cpp (NEW) -- chain a -> b -> c -> out; the MIDDLE Func `b` is specialized, with its own producer `a` (compute_at b.x) injected into each of b's branches, and consumer `c` above it.

[x] Clarify if `Identical branches merge` is intended to be out-of-scope for `micro_halide`.
    If so, I agree with this decision.
    CONFIRMED out-of-scope. loopdoc §15 "Out of scope" now states micro_halide may emit one loop nest per branch (specializations then fallback) WITHOUT merging identical ones; matching Halide's simplify()-driven merge would need true-IR-identity comparison. All examples have structurally distinct branches, so no merge is exercised.
