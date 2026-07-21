#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Stages are scheduled INDEPENDENTLY. f.update(0).split(...) rewrites only the
// update stage's dimension list; the pure stage is untouched.
//
//   s0  f(x) = 0              -> for x                (unchanged)
//   s1  f(x) += in(x, r),
//       update(0).split(x,xo,xi,4)
//                            -> [xi, xo, r]... split adds one loop to the
//                              update stage only:
//
//   produce f:
//     for x:
//       f(...) = ...
//     for x.xo:
//       for x.xi:
//         for r:
//           f(...) = ...

int main()
{
    Var x("x");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x) = 0;

    RDom r(0, 16, "r");
    f(x) += in(x, r);

    Var xo("xo"), xi("xi");
    f.update(0).split(x, xo, xi, 4); // splits the UPDATE stage's x only

    f.print_loop_nest();
}
