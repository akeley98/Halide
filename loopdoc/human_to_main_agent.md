# Human-added Tasks

Grouped into sections; sections are meant to be done in stated order, with tasks within a section not necessarily ordered.

[x] Add the `specialize_tree.cpp` example.
    DONE: examples/specialize_tree.cpp -- sibling cond_b + child cond_c of cond_a -> 4-leaf tree; verified vs real Halide. Cited from §15 "How it becomes loops".
[x] Edit `loopdoc.md` based on comments.
    DONE (all three inline <!-- Human --> comments removed):
      * §1: the nesting detail is now deferred to §15 with a forward ref.
      * §15: "declaration order" defined as the program order of the specialize() calls (first-declared tested first, first match wins).
      * §15: added the flat-vs-nested explanation -- siblings on one handle => flat if/else-if chain; specialize on a returned branch handle => nested if; mixing builds a tree -- citing specialize_tree.cpp (and specialize_nested.cpp).
[x] Move the specialization stubs to common code in `FuncStageImpl` unless there's a non-obvious reason I overlooked why this is a bad idea.
    DONE: specialize / specialize_fail now declared once on FuncStageImpl (defined out of line as template methods, since they return the still-incomplete Stage), removed from Func and Stage. No non-obvious reason against it -- specialize is one operation on whichever stage `stage_index` names, so the base is the right home (and matches Halide, where Func::specialize forwards to the Stage-level op). All specialize examples still compile with micro (and still throw at the stub, pending the micro-agent).
[x] Add ominous comments to `micro_halide.hpp` about not duplicating code in `Func` and `Stage` so I don't have to keep asking for this.
    DONE: a prominent block on FuncStageImpl says to put shared Func/Stage scheduling methods there, never copy-pasted into both classes, and how to handle Stage-returning methods; plus short "inherited from FuncStageImpl; do NOT redeclare" notes left in Func and Stage.
