#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// compute_with, simplest form: f and g are independent compute_root Funcs.
// g.compute_with(f, y) fuses g's loops into f's, sharing the y loop. Below y
// each keeps its own x loop, as siblings, in compute order (parent f first).
//
//   produce f:            # parent, outermost produce
//     produce g:
//       for fused.y:       # shared loop
//         for x: f         # parent body first
//         for x: g
//   consume f:
//     consume g:
//       produce h: ...
int main() {
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), h("h");
    f(x, y) = in(x, y);
    g(x, y) = in(x, y) + 1;
    h(x, y) = f(x, y) + g(x, y);
    f.compute_root();
    g.compute_root();
    g.compute_with(f, y);
    h.print_loop_nest();
}
