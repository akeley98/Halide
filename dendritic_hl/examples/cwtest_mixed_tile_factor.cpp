#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp mixed_tile_factor_test. f, g, h are each tiled with
// DIFFERENT tile factors, then chained-fused at the inner tile var yi:
//   g.compute_with(f, yi); h.compute_with(g, yi).
// (The fuse level names a loop -- yi -- present in all three; the differing
// factors only change bounds, which the harness normalizes away. The original
// used the 6-arg tile overload; here we use the explicit 8-arg form with fresh
// xo/yo outer vars.) input is store_root + compute_at(f, yo). The real test
// realizes Pipeline{f,g,h}; micro cannot, so out reads all three as the single
// output.
int main() {
    try {
        Var x("x"), y("y"), c("c"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
        ImageParam A(type_of<uint8_t>(), 3, "A");
        Func f("f"), g("g"), h("h"), input("input"), out("out");
        input(x, y, c) = A(x, y, c);
        f(x, y) = input(x, y, 0) + input(x, y, 1);
        g(x, y) = input(x, y, 1) + input(x, y, 2);
        h(x, y) = input(x, y, 2) + input(x, y, 1);
        out(x, y) = f(x, y) + g(x, y) + h(x, y);
        f.compute_root();   // f, g, h were Pipeline outputs (implicitly root)
        g.compute_root();
        h.compute_root();
        f.tile(x, y, xo, yo, xi, yi, 32, 16);
        g.tile(x, y, xo, yo, xi, yi, 7, 9);
        h.tile(x, y, xo, yo, xi, yi, 4, 16);
        g.compute_with(f, yi);
        h.compute_with(g, yi);
        input.store_root();
        input.compute_at(f, yo);
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
