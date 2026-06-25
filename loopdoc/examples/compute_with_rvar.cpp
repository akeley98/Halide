#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// compute_with whose fuse level is an RVar. f and g each reduce over a 2-D RDom.
// Their pure stages are fused at the Var y; their UPDATE stages are fused at the
// outer reduction var r.y (an RVar). So the update pair shares y, x and r.y,
// while each member keeps its own inner reduction loop r.x.
//
//   produce g:
//     produce f:
//       for fused.y:              # pure stages fused at y
//         for x: g
//         for x: f
//       for fused.y:              # update stages: shared down to r.y
//         for x:
//           for r.y:              # shared outer reduction var (the RVar fuse level)
//             for r.x: g          # each member's own inner reduction loop
//             for r.x: f
//   consume g: consume f: produce out: ...
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        RDom r(0, 3, 0, 3, "r");
        Func f("f"), g("g"), out("out");
        f(x, y) = 0;
        f(x, y) += in(x + r.x, y + r.y);
        g(x, y) = 0;
        g(x, y) += in(x + r.x, y + r.y);
        out(x, y) = f(x, y) + g(x, y);
        f.compute_root();
        g.compute_root();
        f.compute_with(g, y);                       // pure stages fused at Var y
        f.update().compute_with(g.update(), r.y);   // update stages fused at RVar r.y
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
