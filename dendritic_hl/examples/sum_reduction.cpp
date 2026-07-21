#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A reduction with a free Var on the left: f(x) sums a row of the input.
//
//   s0  f(x) = 0           -> loops over x
//   s1  f(x) += in(x, r)   -> free Var x plus reduction r; the RVar is
//                            innermost, so the stage loops `for x: for r:`
//
//   produce f:
//     for x:
//       f(...) = ...
//     for x:
//       for r:
//         f(...) = ...

int main()
{
    Var x("x");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x) = 0;

    RDom r(0, 64, "r");
    f(x) += in(x, r);

    f.print_loop_nest();
}
