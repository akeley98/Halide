#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// specialize + a compute_at producer (loopdoc.md section 15): a producer filed
// at a loop of the specialized consumer is injected SEPARATELY into each branch,
// resolved against THAT branch's own loop nest (each branch has its own copy of
// the dimension list). So the producer follows the branch's structure.
//
// g is computed at f's inner x in BOTH branches (a single point either way, so
// g's x and y both collapse -- symmetric across branches). The specialization
// splits f's OUTER y, so the specialized branch wraps g in 3 f-loops and the
// fallback in 2, with g injected at the innermost x each time. Verified vs real
// Halide:
//   produce f:
//     for y.yo: for y.yi: for x:  produce g: g(...)=...  consume g: f(...)=...   <- branch
//     for y:              for x:  produce g: g(...)=...  consume g: f(...)=...   <- fallback
//
// (The related "producer at a DIFFERENT loop-name per branch" case -- e.g. g at
// f.y in one branch, f.x in the other -- gives g different bounds per branch, so
// its loop elision differs per branch; that is not expressible with the
// per-producer-stage micro_halide_collapses API and is deferred, see
// progress.txt. This example keeps g's elision symmetric.)
int main() {
    Var x("x"), y("y"), yo("yo"), yi("yi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func g("g"), f("f"), out("out");
    g(x, y) = in(x, y);
    f(x, y) = g(x, y);
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> cond;
    f.specialize(cond).split(y, yo, yi, 4);   // split an outer loop; g still lands at inner x
    g.compute_at(f, x);
    // g at a single (x,y) point in every branch -> both its loops collapse.
    micro_halide_collapses(g, {x, y});
    out.print_loop_nest();
}
