#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// specialize + a compute_root producer (loopdoc.md section 15): the branches of
// a specialized consumer live INSIDE its own `produce`, so a producer computed
// outside (here g at root) is emitted ONCE, before the consumer, and is not
// duplicated per branch. Each branch simply reads the already-produced g.
//
// Verified against real Halide:
//   produce g: for y: for x: g(...)=...
//   consume g:
//     produce f:
//       for y.yo: for x.xo: for y.yi: for x.xi:  f(...)=...   <- specialization
//       for y:    for x:                          f(...)=...   <- fallback
//     consume f: produce out: for y: for x: out(...)=...
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func g("g"), f("f"), out("out");
    g(x, y) = in(x, y);
    f(x, y) = g(x, y);
    out(x, y) = f(x, y);
    g.compute_root();
    f.compute_root();
    Param<bool> cond;
    f.specialize(cond).tile(x, y, xo, yo, xi, yi, 4, 4);
    out.print_loop_nest();
}
