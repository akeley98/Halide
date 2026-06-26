#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE. From test/error/compute_with_crossing_edges2.cpp. f and g each have
// a pure stage plus one update. The fuse edges cross stage indices: f's pure
// stage fuses into g's UPDATE, while f's update fuses into g's PURE stage:
//   f.compute_with(g.update(0), y);
//   f.update(0).compute_with(g, y);
// Halide rejects these crossing edges. The real test realizes a Pipeline{f, g};
// micro cannot, so out = f + g is the output.
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), out("out");
        f(x, y) = in(x, y);
        f(x, y) += in(x, y);
        g(x, y) = in(x, y);
        g(x, y) += in(x, y);
        out(x, y) = f(x, y) + g(x, y);
        f.compute_root();
        g.compute_root();
        f.compute_with(g.update(0), y);
        f.update(0).compute_with(g, y);
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
