#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL: a per-stage loop transform (§9) and a compute_at (§7) into the
// transformed stage, combined. f's update stage is split, and a producer q is
// computed at the split *inner* var -- which exists only in the update stage's
// (post-split) dimension list. q is read only by the update stage, so it is
// injected there, between the split outer and inner loops collapse away to a
// single point:
//
//   produce f:
//     for x:                  # f stage 0 (pure), untouched by the split
//       f(...) = ...
//     for xo:                 # f stage 1, split
//       for xi:
//         produce q:
//           q(...) = ...
//         consume q:
//           f(...) = ...

int main()
{
    Var x("x");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func q("q");
    q(x) = in(x, 0);

    Func f("f");
    f(x) = 0;
    f(x) += q(x); // update stage reads q; loops over x (no RDom needed)

    Var xo("xo"), xi("xi");
    f.update(0).split(x, xo, xi, 4); // split only the update stage

    q.compute_at(f, xi);             // compute q at the update stage's split inner var
    micro_halide_collapses(q, {x});  // q is a single point per xi iteration

    f.print_loop_nest();
}
