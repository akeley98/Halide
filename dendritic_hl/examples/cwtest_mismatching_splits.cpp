#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp mismatching_splits_test. g and h are split with
// DIFFERENT structure but fused at a loop named `y` that ends up at matching
// depth:
//   g: split(y, yo, y, ..) then split(y, y, yi, ..)  -> [.., y, yi, yo] then
//      g.compute_with(h, y)
//   h: reorder(x, c, y) then split(y, yo, y, ..)
// The fuse var `y` (the middle split-product on both) must sit at the same depth
// in both. (vectorize stripped.) The real test realizes Pipeline{h,g}; micro
// cannot realize multiple outputs, so out reads both as the single output.
int main() {
    try {
        Var x("x"), y("y"), c("c"), yi("yi"), yo("yo");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), h("h"), out("out");
        f(x, y, c) = in(x, y) + c;
        h(x, y, c) = f(x, y, c);
        g(x, y) = f(x, y, 2);
        out(x, y) = h(x, y, 0) + g(x, y);
        g.compute_root();   // g, h were Pipeline outputs (implicitly root)
        g.split(y, yo, y, 64).split(y, y, yi, 2).compute_with(h, y);
        h.reorder(x, c, y).split(y, yo, y, 32).compute_root();
        // Only h(x,y,0) is consumed, so h's c extent is 1 and its c loop collapses.
        micro_halide_collapses(h, {c});
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
