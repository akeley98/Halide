#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A specialized PRODUCER (loopdoc.md section 15): specialize is not special to
// the output -- any Func's produce block carries its own branches. Here the
// producer g is specialized (tiled branch + plain fallback), and the consumer f
// reads it normally; g's two branches sit inside `produce g`, then `consume g`
// wraps f as usual.
//
// Verified against real Halide:
//   produce g:
//     for y.yo: for x.xo: for y.yi: for x.xi:  g(...)=...   <- specialization
//     for y:    for x:                          g(...)=...   <- fallback
//   consume g:
//     produce f: for y: for x: f(...)=...
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
    g.specialize(cond).tile(x, y, xo, yo, xi, yi, 4, 4);
    out.print_loop_nest();
}
