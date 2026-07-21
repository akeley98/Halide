#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE: the two stages must agree on their loop nest from the outermost loop
// down to the fuse level -- in particular the NUMBER of loops at/above the fuse
// level must match. f has loops (y outer, x inner); g.reorder(y, x) makes g's
// loops (x outer, y inner). Fusing at y then spans 1 loop in f (y is outermost)
// but 2 in g (y is innermost), so the fused-dimension counts disagree -- Halide
// errors "Invalid compute_with: # of fused dims of f.s0 and g.s0 do not match".
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), h("h");
        f(x, y) = in(x, y);
        g(x, y) = in(x, y) + 1;
        h(x, y) = f(x, y) + g(x, y);
        f.compute_root();
        g.compute_root();
        g.reorder(y, x);          // g's loops become (x outer, y inner)
        g.compute_with(f, y);     // illegal: y is at a different depth in f vs g
        h.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
