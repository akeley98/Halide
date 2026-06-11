#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A dimension created by `split` is a first-class compute_at site.
//
// out.split(x, xo, xi, 8) gives out the dimension list [xi, xo, y], loops
// `for y: for xo: for xi:`. g is computed at the split OUTER loop xo, so it is
// injected just inside xo and outside xi -- with xi falling inside g's consume:
//
//   produce out:
//     for y:
//       for xo:
//         produce g:
//           for x:
//             g(...) = ...
//         consume g:
//           for xi:
//             out(...) = ...
//
// g needs a strip of 8 in x per xo iteration (one point in y), so its x loop
// survives and its y loop collapses.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func out("out");
    out(x, y) = g(x, y);

    Var xo("xo"), xi("xi");
    out.split(x, xo, xi, 8);
    g.compute_at(out, xo);
    micro_halide_collapses(g, {y}); // g spans the xi strip in x, one point in y

    out.print_loop_nest();
}
