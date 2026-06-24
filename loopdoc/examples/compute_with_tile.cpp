#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// compute_with interacting with tile. Both f and g are tiled identically; the
// two stages must agree on their schedule from the outermost loop down to the
// fuse level. Fusing at the outer tile var xo shares the outer tile loops
// (yo, xo) and leaves each member's inner tile loops (yi, xi) as its own
// siblings below.
//
//   produce f:
//     produce g:
//       for fused.yo:          # shared outer tile loops (down to xo)
//         for fused.xo:
//           for yi: for xi: f  # f's own inner tile loops
//           for yi: for xi: g  # g's own inner tile loops
//   consume f: consume g: produce h: ...
int main() {
    try {
        Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), h("h");
        f(x, y) = in(x, y);
        g(x, y) = in(x, y) + 1;
        h(x, y) = f(x, y) + g(x, y);
        f.compute_root();
        g.compute_root();
        f.tile(x, y, xo, yo, xi, yi, 4, 4);
        g.tile(x, y, xo, yo, xi, yi, 4, 4);
        g.compute_with(f, xo);
        h.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
