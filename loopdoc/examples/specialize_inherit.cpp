#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// specialize inheritance (loopdoc.md section 15): a specialization's schedule is
// a COPY of the schedule *so far* -- every directive issued before the
// specialize() call. Directives issued on the branch handle add to that copy.
//
// Here f is tiled BEFORE specialize, and the specialization adds a split of the
// tile's inner x. So the specialized branch has tile + split (5 loops); the
// fallback has the tile only (4 loops). Verified against real Halide:
//   produce f:
//     for y.yo: for x.xo: for y.yi: for x.xi.xi: for x.xi.xii:  f(...)=...  <- branch
//     for y.yo: for x.xo: for y.yi: for x.xi:                    f(...)=...  <- fallback
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi"), xii("xii");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);
    out(x, y) = f(x, y);
    f.compute_root().tile(x, y, xo, yo, xi, yi, 4, 4);   // applies to ALL branches
    Param<bool> cond;
    f.specialize(cond).split(xi, xi, xii, 2);            // branch inherits the tile, adds split
    out.print_loop_nest();
}
