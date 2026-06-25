# Human-added Tasks

Grouped into sections; sections are meant to be done in stated order, with tasks within a section not necessarily ordered.

## RVar

- [ ] `RVar` isn't accepted by `micro_halide::compute_with`.
      Fix and test this.
      Minimal authorized modifications to `micro_halide` to test this for the main agent to do:
      * First move the two `compute_with` implementations into the common `FuncStageImpl` class and get rid of the TODOs for the micro-agent.
      * Then add a new `compute_with` for `RVar`. It seems the `Var` is just reduced to a string name so this should be easy to do without really adding any new logic.
- [ ] The "Var or RVar"-ness may be part of the "matching loop nests down to `v`" criteria so the above may expose a real doc/`micro_halide` gap.
  Don't fix `micro_halide` if this is the case, but update the doc (will launch micro-agent later).

## Copy Halide Tests

- [ ] Look at `test/correctness/compute_with.cpp` (about 2000 lines of code) and copy examples from there.
      Get rid of trace and realize code, and get rid of anything not supported by `micro_halide`.
      (Report if this seems to undermine the example).
      Print the loop nest of the func that was realized in the original test.
- [ ] Adapt some of those tests into negative examples.

## More tests

Ignore cases that already exist or were covered by the `compute_with.cpp` copying.

- [ ] Cases of interaction between `store_at` and fused groups causing the site func argument to `store_at` to be wrong (a fused away child)
- [ ] Cases of indirect producer/consumer relations (`f->g->h`) potentially making some `compute_with` not valid
- [ ] Adversarial cases of `store:` being printed for multiple functions with the same site for `store_at`, with interaction with fused groups.
- [ ] In general just make complicated versions of existing cases with more transitivity and indirect producer/consumer relations.
      The latter has been a profitable source of bugs in the past.
- [ ] `rfactor`-generated functions and `compute_with`. Test some interesting interactions.
