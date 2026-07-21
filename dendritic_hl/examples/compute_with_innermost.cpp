#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// Fusing at the innermost dimension: g.compute_with(f, x) shares ALL of f's
// loops (y and x). Because the fuse level is innermost, there are no per-member
// sub-loops -- both members' leaves sit directly in the shared inner loop, in
// compute order (parent f first).
//
//   produce f:
//     produce g:
//       for fused.y:
//         for fused.x:
//           f(...) = ...
//           g(...) = ...
//   consume f: consume g: produce h: ...
int main() {
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), h("h");
    f(x, y) = in(x, y);
    g(x, y) = in(x, y) + 1;
    h(x, y) = f(x, y) + g(x, y);
    f.compute_root();
    g.compute_root();
    g.compute_with(f, x);
    h.print_loop_nest();
}
