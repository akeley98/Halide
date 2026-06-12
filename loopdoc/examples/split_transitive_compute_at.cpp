#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL: transitivity (§7 "indirect consumer") x split (§9).
// h splits x into xo, xi; g is computed at the OUTER split var xo; f -- read
// only by g -- is computed at xo as well. f.compute_at(h, xo) is legal because
// xo encloses g; the inner split loop xi lives in `consume g`, after g.
//
//   produce h:
//     for y:
//       for xo:
//         produce f:        # f at xo, before g
//           for x:
//             f(...) = ...
//         consume f:
//           produce g:
//             for x:
//               g(...) = ...
//           consume g:
//             for xi:
//               h(...) = ...

int main()
{
    Var x("x"), y("y"), xo("xo"), xi("xi");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);
    Func g("g");
    g(x, y) = f(x, y) + f(x + 1, y);
    Func h("h");
    h(x, y) = g(x, y);

    h.split(x, xo, xi, 8);
    g.compute_at(h, xo);
    f.compute_at(h, xo); // legal: xo encloses g (hence f's use)

    micro_halide_collapses(g, {y}); // g spans the xi strip in x, one point in y
    micro_halide_collapses(f, {y});

    h.print_loop_nest();
}
