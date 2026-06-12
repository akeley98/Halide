"inline" is the name of a compute level, not a synonym for "disappears." Three pieces of source settle it:

`Func::compute_inline()` doc (`src/Func.h:2568`) — Halide's own words:

> "Aggressively inline all uses of this function. This is the default schedule … For a Func with an update definition, that means it gets computed as close to the innermost loop as possible."

So Halide explicitly applies "inline" to update Funcs and says it means computed at the innermost loop (materialized), not textual pasting.

`Function::can_be_inlined()` (`src/Function.cpp:1074`) = `is_pure() && no specializations`, where `is_pure()` (`Function.h:185`) = "has a pure definition and no update/extern definition." So a Func with updates cannot be (textually) inlined — yet its default compute level is still `LoopLevel::inlined()`.

The lowering split (`src/ScheduleFunctions.cpp`): a Func whose `compute_level().is_inlined()` is true is handled two ways — pure ones are substituted into callers; non-pure ones hit `inline_to_provide` (~1358), which calls build_realize — i.e. Halide does inject a realization (a produce block) for an "inlined" update Func.
