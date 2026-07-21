#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL: transitivity (§7 "indirect consumer") x fuse (§9).
// h fuses its loops into xy; g is computed at that fused loop; f -- read only by
// g -- is computed at the SAME fused loop of h. f.compute_at(h, xy) is legal
// because the fused loop encloses g, hence f's use.
//
//   produce h:
//     for xy:
//       produce f:        # f at h's fused loop, before g
//         for x:
//           f(...) = ...
//       consume f:
//         produce g:      # g (single point) computed at the fused loop
//           g(...) = ...
//         consume g:
//           h(...) = ...

int main()
{
    Var x("x"), y("y"), xy("xy");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);
    Func g("g");
    g(x, y) = f(x, y) + f(x + 1, y);
    Func h("h");
    h(x, y) = g(x, y);

    h.fuse(x, y, xy);
    g.compute_at(h, xy);
    f.compute_at(h, xy); // legal: fused loop encloses g (hence f's use)

    micro_halide_collapses(g, {x, y}); // g is a single point per xy
    micro_halide_collapses(f, {y});    // f spans x (2-tap), one point in y

    h.print_loop_nest();
}
