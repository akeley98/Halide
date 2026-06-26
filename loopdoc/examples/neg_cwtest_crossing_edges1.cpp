#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE. From test/error/compute_with_crossing_edges1.cpp. f has a pure stage
// plus two updates; two of f's stages fuse into the SAME parent g but at
// non-adjacent stage indices (f.s0 and f.s2 fuse into g while f.s1 does not),
// producing "crossing" fuse edges that Halide rejects. The real test realizes a
// Pipeline{f, g}; micro cannot, so out = f + g is the output.
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), out("out");
        f(x, y) = in(x, y);
        f(x, y) += in(x, y);
        f(x, y) += in(x, y);
        g(x, y) = in(x, y);
        out(x, y) = f(x, y) + g(x, y);
        f.compute_root();
        g.compute_root();
        f.compute_with(g, y);
        f.update(1).compute_with(g, y);
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
