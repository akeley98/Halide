#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// The intermediate Func created by rfactor is an ordinary Func with its OWN
// default schedule. It is non-pure (it has an update stage), so its default is
// the non-pure "inline" default (loopdoc.md section 11): it is REALIZED at its
// innermost use -- which is inside f's merge update stage -- and recomputed for
// each value of the enclosing loops. Here f's merge stage reads f_intm(x, r.y)
// inside `for x: for r(=r.y):`, so the whole intermediate is materialized there.
// At that site only one x and one u(=r.y) are needed, so f_intm's x and u loops
// collapse to points:
//
//   produce f:
//     for x:                       # f init
//     for x:
//       for r(=r.y):                # f merge: reduce over r.y
//         produce f_intm:           # intermediate recomputed per (x, r.y)
//           f_intm(...) = ...         #   init: x,u collapsed
//           for r(=r.x):             #   partial: reduce over r.x
//         consume f_intm:
//           f(...) = ...

int main()
{
    Var x("x"), u("u");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    RDom r(0, 10, 0, 10, "r");
    f(x) = 0;
    f(x) += in(r.x, r.y);

    Func intm = f.update(0).rfactor(r.y, u);
    // No compute_root: the intermediate keeps its default (non-pure inline).
    micro_halide_collapses(intm, {x, u});            // init: only a point of x,u here
    micro_halide_collapses(intm.update(0), {x, u});  // partial: only a point of x,u here

    f.print_loop_nest();
}
