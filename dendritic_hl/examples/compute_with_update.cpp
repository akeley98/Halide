#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// compute_with is per stage. Each Func here has an init stage and one update
// stage; BOTH are fused (the init via compute_with, the update via
// update().compute_with). g is the parent (f.compute_with(g, ...)). Each fused
// stage-pair shares its own loop nest, the two appearing as consecutive
// siblings inside the single produce block.
//
//   produce g:
//     produce f:
//       for fused.y:       # fused init stages
//         for x: g
//         for x: f
//       for fused.y:       # fused update stages
//         for x: g
//         for x: f
//   consume g: consume f: produce h: ...
int main() {
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), h("h");
    f(x, y) = in(x, y);
    f(x, y) += in(x, y);
    g(x, y) = in(x, y);
    g(x, y) += in(x, y);
    h(x, y) = f(x, y) + g(x, y);
    f.compute_root();
    g.compute_root();
    f.compute_with(g, y);
    f.update().compute_with(g.update(), y);
    h.print_loop_nest();
}
