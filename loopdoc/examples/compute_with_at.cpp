#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// A fused group need not be at root. Here f and g are both computed at out's y
// loop and fused at x; the whole group sits inside out's y loop. Each member is
// computed per out-y, so its own y collapses to a point (declared elision),
// leaving just the shared x.
//
//   produce out:
//     for y:
//       produce f:
//         produce g:
//           for fused.x:
//             f(...) = ...
//             g(...) = ...
//       consume f:
//         consume g:
//           for x: out(...) = ...
int main() {
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), out("out");
    f(x, y) = in(x, y);
    g(x, y) = in(x, y) + 1;
    out(x, y) = f(x, y) + g(x, y);
    f.compute_at(out, y);
    g.compute_at(out, y);
    g.compute_with(f, x);
    micro_halide_collapses(f, {y});
    micro_halide_collapses(g, {y});
    out.print_loop_nest();
}
