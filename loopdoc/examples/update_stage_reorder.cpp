#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// reorder applies per stage and treats an RVar like a Var in the dimension
// list. The update stage's default list is [r, x] (r innermost); reordering it
// to put x innermost swaps them.
//
//   s0  f(x, y) = 0                 -> for y: for x
//   s1  f(x, y) += in(x+r, y),
//       update(0).reorder(x, r)     -> x innermost in the update stage:
//
//   produce f:
//     for y:
//       for x:
//         f(...) = ...
//     for y:
//       for r:
//         for x:
//           f(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = 0;

    RDom r(0, 5, "r");
    f(x, y) += in(x + r, y);

    f.update(0).reorder(x, r); // put the free Var x inside the reduction loop

    f.print_loop_nest();
}
