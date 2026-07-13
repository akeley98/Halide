# `micro_halide`: how these docs were validated (and what the examples' annotations mean)

Detail companion to the main [loopdoc.md](../loopdoc.md). This file is *not* about
Halide itself — it explains the testing apparatus these documents were written
against, so that the machinery visible in the `examples/` programs (an unfamiliar
include, a `micro_halide_collapses(...)` call) is not mysterious. A reader who
only wants to understand Halide loop nests can skip it.

## What `micro_halide` is

The docs were developed by *dogfooding*: a deliberately tiny re-implementation of
a subset of Halide's scheduling and loop-nest logic, `micro_halide`
([../micro_halide/micro_halide.hpp](../micro_halide/micro_halide.hpp)), was built
**from these documents alone** and then checked to produce the same loop nests as
real Halide on a corpus of small example programs
([../examples/](../examples/)). Where `micro_halide` and Halide disagreed, either
the documentation was incomplete/wrong (and got fixed) or the case was declared
out of scope. So the prose here is not just descriptive — most claims were
reconstructed by an independent implementer and tested.

Each example compiles two ways from the same source: against real Halide, and
against `micro_halide` (via `-DUSE_MICRO_HALIDE`), which is the reason every
example opens with

```cpp
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
...
#else
#include "Halide.h"
...
#endif
```

## The comparison is structural: `canonicalize.py`

The two `print_loop_nest()` outputs are compared *after* canonicalization
([../canonicalize.py](../canonicalize.py)), which keeps only what these documents
treat as significant and drops the rest:

* **Kept:** node kind (`produce`/`consume`/`store`/`for`/leaf), Func identity as a
  positional id (`F0`, `F1`, … in first-appearance order), loop nesting depth and
  sibling order, and each loop's **type** and **`<device_api>`** (§17 in the main
  doc / [fortype.md](fortype.md)).
* **Dropped:** exact loop-variable names, and constant loop bounds (`for x in
  [0, 7]`).

This is why the main document says it "ignores loop-variable names and constant
bounds," identifies Funcs positionally, and warns that a plain serial `reorder` is
invisible while a *typed* one is not: those are exactly this canonicalizer's
kept/dropped sets. A *negative* example is one both Halide and `micro_halide` must
**reject** with an error rather than print a nest.

## `micro_halide_collapses`: the declared-elision annotation

The one modeling gap that shows up *in the example source* is loop elision (§7 in
the main doc): a `compute_at` producer emits a loop only where the required region
spans more than one point, and a single-point dimension collapses to no `for`
line. **Which** loops collapse depends on bounds inference over the actual index
arithmetic — which `micro_halide` deliberately does not implement. So the split is:

* **loop *structure*** (produce/consume placement, ordering, which loops exist) is
  derived from the schedule, taught in the main document, and reproduced by
  `micro_halide`;
* **loop *elision*** (which of those loops then have extent 1) is *declared* per
  example rather than derived, with

  ```cpp
  f.compute_at(output, x);
  micro_halide_collapses(f, {x});   // f's x loop has extent 1 here; elide it
  ```

`micro_halide_collapses(f, {vars...})` is a **no-op under real Halide** (Halide
figures the extents out itself); it only tells `micro_halide` which loops to drop.
It is declared per stage, mirroring the schedule API: `micro_halide_collapses(f,
…)` targets the pure stage and `micro_halide_collapses(f.update(N), …)` an update
stage. This is the mechanism behind every "which loops collapse is a bounds
question, not derived here" aside in the main doc and the other detail files.

Consequences of having no bounds analysis, referenced elsewhere:

* **Extent-1 GPU survival** ([fortype.md](fortype.md)): the rule that a
  1-iteration loop is elided only when its `device_api` is `None` is *documented
  but not tested* — `micro_halide` never discovers extent 1 on its own, and the
  collapse annotation is already the answer key, so there is no honest test of the
  rule itself (only of directive *structure*, e.g. that `gpu_single_thread`
  emits a block+thread+serial nest).
* **Per-branch / per-host-stage elision asymmetry** ([specialize.md](specialize.md),
  main §7): the annotation is keyed per producer-stage, not per branch or per
  host-stage injection, so asymmetric-elision cases are left unmodeled.
* **`select`-pruning** ([specialize.md](specialize.md)): needs `Expr` analysis
  `micro_halide` does not do.
