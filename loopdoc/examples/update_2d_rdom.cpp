#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A 2-D reduction domain contributes TWO nested reduction loops.
//
//   s0  f(x, y) = 0                       -> for y: for x
//   s1  f(x, y) += in(x+r.x, y+r.y)       -> free Vars x,y plus RVars r.x,r.y;
//                                           the RVars are innermost (first-
//                                           declared r.x is the outer of them):
//
//   produce f:
//     for y:
//       for x:
//         f(...) = ...
//     for y:
//       for x:
//         for r.x:
//           for r.y:
//             f(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = 0;

    RDom r(0, 3, 0, 3, "r");
    f(x, y) += in(x + r.x, y + r.y);

    f.print_loop_nest();
}
