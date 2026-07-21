#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// specialize_fail (loopdoc.md section 15): terminates the specialization chain
// with a runtime error instead of a fallback. In the loop nest its "else" is an
// assert that carries no loops, so print_loop_nest emits ONLY the specialization
// branches -- the default nest is absent.
//
// Here the sole specialization tiles f; there is no fallback subtree. Verified
// against real Halide:
//   produce f:
//     for y.yo: for x.xo: for y.yi: for x.xi:  f(...)=...   <- specialization only
//   consume f: produce out: for y: for x: out(...)=...
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> cond;
    f.specialize(cond).tile(x, y, xo, yo, xi, yi, 4, 4);
    f.specialize_fail("no default case");   // drops the fallback nest
    out.print_loop_nest();
}
