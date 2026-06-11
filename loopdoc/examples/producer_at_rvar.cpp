#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// An RVar loop is a valid compute_at site. p is read only inside f's reduction,
// so p.compute_at(f, r) injects p inside the update stage's `for r` loop:
//
//   produce f:
//     for x:                  # stage 0
//       f(...) = ...
//     for x:                  # stage 1
//       for r:
//         produce p:
//           p(...) = ...
//         consume p:
//           f(...) = ...
//
// p needs a single point per reduction iteration, so it emits no loops.

int main()
{
    Var x("x");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = in(x, 0) * 2;

    Func f("f");
    f(x) = 0;
    RDom r(0, 16, "r");
    f(x) += p(r);

    p.compute_at(f, r); // compute p inside the reduction loop
    micro_halide_collapses(p, {x});

    f.print_loop_nest();
}
