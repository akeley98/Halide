#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Adversarial (nested specialize on an UPDATE stage, loopdoc.md section 15).
// The specialize handle for an update stage is a branch off stage 1, and it is
// itself specialized (a child), so the branch-handle / nesting machinery must
// work when the owning stage is an update, not just the pure stage. The pure
// stage (0) stays a single nest.
//
//   Stage u = f.update(0).specialize(c1);  u.tile(...);  u.specialize(c2).split(...);
//
// Verified against real Halide -- four nests under one `produce f`:
//   for y: for x: f(...)=...                                  <- stage 0 (pure)
//   for yo: for xo: for yi: for xi.cx: for xi.cxi: f(...)=... <- u & c2 (tile+split)
//   for yo: for xo: for yi: for xi: f(...)=...                <- u & !c2 (tile)
//   for y: for x: f(...)=...                                  <- !c1 fallback (update)
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi"), cx("cx"), cxi("cxi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);      // stage 0 (pure), unspecialized
    f(x, y) += in(x, y);     // stage 1 = update(0), nested specialize tree here
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> c1, c2;
    Stage u = f.update(0).specialize(c1);          // branch handle on an update stage
    u.tile(x, y, xo, yo, xi, yi, 4, 4);            // c1 branch tiled (c2 inherits)
    u.specialize(c2).split(xi, cx, cxi, 2);        // nested child on the update branch
    out.print_loop_nest();
}
