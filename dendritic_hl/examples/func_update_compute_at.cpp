#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// compute_at moves a Func's ENTIRE multi-stage produce to the site. f has a
// pure stage and an update stage; f.compute_at(g, y) drops both stages inside
// g's y loop:
//
//   produce g:
//     for y:
//       produce f:
//         for x:            # f stage 0
//           f(...) = ...
//         for x:            # f stage 1
//           for r:
//             f(...) = ...
//       consume f:
//         for x:
//           g(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x) = x;
    RDom r(0, 16, "r");
    f(x) += in(r, 0);

    Func g("g");
    g(x, y) = f(x);

    f.compute_at(g, y); // whole produce f (both stages) moves under g's y loop

    g.print_loop_nest();
}
