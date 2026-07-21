#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§7 indirect consumer, TWO levels deep): f -> g -> k -> h.
// k is computed at h's y; f is read only by g (read by k). f.compute_at(h, y) is
// legal: f's use is transitively inside h's y loop (through k, then g). The
// injection must see the use TWO producers down.
//
//   produce h:
//     for y:
//       produce f:              # f at h.y, before k
//         for x: f(...) = ...
//       consume f:
//         produce k:
//           for x:
//             produce g:        # g (single point) at k's x
//               g(...) = ...
//             consume g:
//               k(...) = ...
//         consume k:
//           for x: h(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);
    Func g("g");
    g(x, y) = f(x, y) + f(x + 1, y);
    Func k("k");
    k(x, y) = g(x, y);
    Func h("h");
    h(x, y) = k(x, y);

    k.compute_at(h, y);
    g.compute_at(k, x);
    f.compute_at(h, y); // legal: f's use is transitively inside h.y (via k, g)

    micro_halide_collapses(g, {x, y}); // g single point at k's x
    micro_halide_collapses(k, {y});    // k spans x per h.y, one point in y
    micro_halide_collapses(f, {y});    // f spans x (2-tap), one point in y

    h.print_loop_nest();
}
