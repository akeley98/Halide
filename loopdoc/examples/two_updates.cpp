#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Three stages, printed in order inside one `produce f`.
//
//   s0  f(x) = 0           -> for x
//   s1  f(x) += in(r)      -> for x: for r
//   s2  f(x) *= 2          -> for x   (no RDom: just the free Var)
//
//   produce f:
//     for x:
//       f(...) = ...
//     for x:
//       for r:
//         f(...) = ...
//     for x:
//       f(...) = ...

int main()
{
    Var x("x");

    ImageParam in(type_of<int>(), 1, "in");

    Func f("f");
    f(x) = 0;

    RDom r(0, 32, "r");
    f(x) += in(r);
    f(x) *= 2;

    f.print_loop_nest();
}
