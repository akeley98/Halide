#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Adversarial (per-stage specialization indexing, loopdoc.md sections 1, 15).
// f has THREE stages; only the MIDDLE one (update(0) = stage 1) is specialized.
// The specialization list is per stage, so the branches must attach to stage 1
// alone -- the pure stage (0) BEFORE it and the update(1) stage (2) AFTER it must
// each stay a single, unspecialized nest. This guards against an off-by-one or
// "specialize bleeds into neighbouring stages" bug.
//
// Verified against real Halide -- four nests under one `produce f`:
//   for y: for x: f(...)=...                      <- stage 0 (pure), unspecialized
//   for y: for x.ax: for x.axi: f(...)=...        <- stage 1 branch (split)
//   for y: for x: f(...)=...                      <- stage 1 fallback
//   for y: for x: f(...)=...                      <- stage 2 (update(1)), unspecialized
int main() {
    Var x("x"), y("y"), ax("ax"), axi("axi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);      // stage 0 (pure)
    f(x, y) += in(x, y);     // stage 1 = update(0)  <- the only specialized stage
    f(x, y) *= in(x, y);     // stage 2 = update(1)
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> cond;
    f.update(0).specialize(cond).split(x, ax, axi, 8);
    f.update(1).unscheduled();   // stage 2 intentionally default (suppress warning)
    out.print_loop_nest();
}
