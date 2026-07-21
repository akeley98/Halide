#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE: the fuse level must name a loop that exists (by name) in BOTH fused
// stages. Here only f is split into (xo, xi); g still has plain x and so has no
// xo loop. g.compute_with(f, xo) is illegal -- Halide errors
// "Invalid compute_with: cannot find xo in g.s0".
int main() {
    try {
        Var x("x"), y("y"), xo("xo"), xi("xi");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), h("h");
        f(x, y) = in(x, y);
        g(x, y) = in(x, y) + 1;
        h(x, y) = f(x, y) + g(x, y);
        f.compute_root();
        g.compute_root();
        f.split(x, xo, xi, 8);     // only f has xo
        g.compute_with(f, xo);     // illegal: g has no xo loop
        h.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
