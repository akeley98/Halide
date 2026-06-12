#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL: transitivity (§7 "indirect consumer") x tile (§9).
// h is tiled (xo, yo, xi, yi); g is computed at the tile-outer xo; f -- read
// only by g -- is also computed at xo. f.compute_at(h, xo) is legal (xo encloses
// g); the inner tile loops yi, xi live in `consume g`.
//
//   produce h:
//     for yo:
//       for xo:
//         produce f:        # f at xo, before g
//           for y: for x: f(...) = ...
//         consume f:
//           produce g:
//             for y: for x: g(...) = ...
//           consume g:
//             for yi: for xi: h(...) = ...

int main()
{
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);
    Func g("g");
    g(x, y) = f(x, y) + f(x + 1, y);
    Func h("h");
    h(x, y) = g(x, y);

    h.tile(x, y, xo, yo, xi, yi, 8, 8);
    g.compute_at(h, xo);
    f.compute_at(h, xo); // legal: xo encloses g; f spans the tile in x and y

    h.print_loop_nest();
}
