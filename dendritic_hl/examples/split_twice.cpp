#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL: a dimension PRODUCED by a split can itself be split again.
//
// f.split(x, xo, xi, 8) gives list [xi, xo, y]; then splitting the freshly
// created inner var xi -- f.split(xi, xio, xii, 2) -- replaces xi with
// [xii, xio], giving [xii, xio, xo, y] and printing four loops:
//
//   produce f:
//     for y:
//       for xo:
//         for xio:
//           for xii:
//             f(...) = ...
//
// Each split adds exactly one loop, regardless of whether the var being split is
// an original dimension or one a previous split created.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);

    Var xo("xo"), xi("xi"), xio("xio"), xii("xii");
    f.split(x, xo, xi, 8).split(xi, xio, xii, 2);

    f.print_loop_nest();
}
