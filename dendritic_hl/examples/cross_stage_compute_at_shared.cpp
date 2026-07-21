#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (positive complement of neg_compute_at_update_rvar.cpp): the
// legal-site rule spans all stages, and a loop SHARED by every using stage is a
// legal site. Here p is read by both f's pure stage (f(x) = p(x)) and f's
// update stage (f(x) += p(x) + in(x, r)) -- both at the single point x. The `x`
// loop exists in both stages, so p.compute_at(f, x) is legal, and p is injected
// into BOTH stages at their x loop (a single point each, so p emits no loops):
//
//   produce f:
//     for x:                # stage 0
//       produce p:
//         p(...) = ...
//       consume p:
//         f(...) = ...
//     for x:                # stage 1
//       produce p:
//         p(...) = ...
//       consume p:
//         for r:
//           f(...) = ...

int main()
{
    Var x("x");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = in(x, 0);

    Func f("f");
    f(x) = p(x);              // pure stage reads p(x)
    RDom r(0, 10, "r");
    f(x) += p(x) + in(x, r);  // update stage also reads p(x), same point

    p.compute_at(f, x);       // x is shared by both stages -> legal
    micro_halide_collapses(p, {x});

    f.print_loop_nest();
}
