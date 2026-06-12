#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§11, transitive): the default inline of a NON-PURE Func inside a
// chain h <- g <- f, where g is computed at h's y loop. f is unscheduled
// (inline) and non-pure, so it is materialized at the innermost point of its
// use -- which is inside g (itself nested in h's y loop). So f's produce block
// lands at g's innermost (x) loop, recomputed per (g.y, g.x), with f's own y
// collapsing (a single point per use) and its x and reduction r surviving:
//
//   produce h:
//     for y:
//       produce g:
//         for y:
//           for x:
//             produce f:          # f materialized at g's innermost loop
//               for x:            # f stage 0 (y collapsed)
//                 f(...) = ...
//               for x:            # f stage 1
//                 for r:
//                   f(...) = ...
//             consume f:
//               g(...) = ...
//       consume g:
//         for x:
//           h(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);
    RDom r(0, 4, "r");
    f(x, y) += in(x + r, y); // f non-pure, left inline

    Func g("g");
    g(x, y) = f(x, y) + f(x + 1, y);

    Func h("h");
    h(x, y) = g(x, y) + g(x, y + 1);

    g.compute_at(h, y);

    micro_halide_collapses(f, {y});           // f's y is a single point at the use
    micro_halide_collapses(f.update(0), {y});

    h.print_loop_nest();
}
