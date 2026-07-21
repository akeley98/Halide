#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// Three-member fused group. f is the parent of both g and h. The two member
// orders diverge:
//   * compute (body) order: parent first, then realization tie-break -> f, g, h
//   * produce nesting: parent outermost, then tie-break REVERSED  -> f, h, g
//
//   produce f:
//     produce h:
//       produce g:
//         for fused.y:
//           for x: f
//           for x: g
//           for x: h
//   consume f: consume h: consume g: produce out: ...
int main() {
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), h("h"), out("out");
    f(x, y) = in(x, y);
    g(x, y) = in(x, y) + 1;
    h(x, y) = in(x, y) + 2;
    out(x, y) = f(x, y) + g(x, y) + h(x, y);
    f.compute_root();
    g.compute_root();
    h.compute_root();
    g.compute_with(f, y);
    h.compute_with(f, y);
    out.print_loop_nest();
}
