#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// compute_with at a split-produced level. Both f and g split x into (xo, xi)
// identically -- the schedules must agree from the outermost loop down to the
// fused level. g.compute_with(f, xo) shares the y and xo loops; below xo each
// keeps its own xi loop, as siblings.
//
//   produce f:
//     produce g:
//       for fused.y:
//         for fused.xo:
//           for xi: f(...) = ...
//           for xi: g(...) = ...
//   consume f:
//     consume g:
//       produce h: ...
int main() {
    Var x("x"), y("y"), xo("xo"), xi("xi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), h("h");
    f(x, y) = in(x, y);
    g(x, y) = in(x, y) + 1;
    h(x, y) = f(x, y) + g(x, y);
    f.compute_root();
    g.compute_root();
    f.split(x, xo, xi, 8);
    g.split(x, xo, xi, 8);
    g.compute_with(f, xo);
    h.print_loop_nest();
}
