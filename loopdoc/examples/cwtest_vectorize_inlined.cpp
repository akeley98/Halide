#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp vectorize_inlined_test. g reads an inlined helper `inl`
// (itself pure + update, left inline), h reads f directly. g and h are each
// split twice on y, then g.compute_with(h, y). (vectorize stripped.) The real
// test realizes Pipeline{h, g}; micro cannot, so out reads both as the single
// output.
int main() {
    try {
        Var x("x"), y("y"), c("c"), yi("yi"), yo("yo");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func input("input"), f("f"), g("g"), h("h"), inl("inl"), out("out");
        input(x, y) = in(x, y);
        f(x, y, c) = input(x, y) + c;
        h(x, y, c) = f(x, y, c);
        inl(x, y) = f(x, y, 0);
        inl(x, y) += f(x, y, 2);
        g(x, y) = inl(x, y);
        out(x, y) = h(x, y, 0) + g(x, y);
        g.compute_root();   // g, h were Pipeline outputs (implicitly root)
        g.split(y, yo, y, 64).split(y, y, yi, 2).compute_with(h, y);
        h.reorder(x, c, y).split(y, yo, y, 32).split(y, y, yi, 1).compute_root();
        // Only h(x,y,0) is consumed, so h's c extent is 1 and its c loop collapses.
        // h's innermost split factor is 1, so its yi loop also collapses.
        micro_halide_collapses(h, {c, yi});
        // inl (pure + update) is realized per g-point, so its own y,x collapse.
        micro_halide_collapses(inl, {x, y});
        micro_halide_collapses(inl.update(), {x, y});
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
