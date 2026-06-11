#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// The DEFAULT schedule for a Func with update definitions.
//
// A Func with updates cannot be textually inlined (a reduction is not an
// expression). Its default `inline` level therefore means: materialize it at
// the innermost point where it is used -- a produce/consume block wrapped around
// the consumer's innermost loop, recomputed every iteration (the same place and
// frequency inlining a pure Func would re-evaluate it). f is unscheduled here;
// g reads f(x) at a single point, so f lands inside g's x loop, and f's own x
// loop collapses in BOTH stages (single point per (x,y)); only the reduction
// loop r survives:
//
//   produce g:
//     for y:
//       for x:
//         produce f:
//           f(...) = ...        # stage 0 (x collapsed)
//           for r:
//             f(...) = ...      # stage 1 (x collapsed, r survives)
//         consume f:
//           g(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x) = 0;
    RDom r(0, 10, "r");
    f(x) += in(x, r);

    Func g("g");
    g(x, y) = f(x); // f unscheduled -> default: realized at this innermost use

    micro_halide_collapses(f, {x});            // pure stage: x is a single point
    micro_halide_collapses(f.update(0), {x});  // update stage: x is a single point

    g.print_loop_nest();
}
