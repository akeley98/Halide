#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// What happens when a Func is compute_at'd at a loop level that is itself
// elided?
//
// `h` is computed at the output's x loop. The output reads h with an x-stencil
// (survives) but pointwise in y, so h's own y loop is elided. We then compute
// `p` at h's *y* loop -- the elided one.
//
// Halide still injects p's realization at that position: the elided loop
// contributes no `for` line, but it remains a valid injection site. So p's
// produce/consume appears as a prefix of h's body, OUTSIDE h's surviving x
// loop:
//
//   produce h:
//     produce p:
//       for y:
//         for x:
//           p(...) = ...
//     consume p:
//       for x:
//         h(...) = ...
//
// (p itself is read with a y-stencil by h, so neither of p's loops elides.)

int main()
{
    Var x("x"), y("y");

    ImageParam input(type_of<uint8_t>(), 2, "input");

    Func p("p");
    p(x, y) = input(x, y) + 1;

    Func h("h");
    h(x, y) = p(x, y) + p(x, y + 1); // y-stencil on p

    Func output("output");
    output(x, y) = h(x, y) + h(x + 1, y); // x-stencil on h, pointwise in y

    h.compute_at(output, x);
    collapses(h, {y}); // h's y loop is elided (pointwise read in y)

    p.compute_at(h, y); // computed at h's elided y loop

    output.print_loop_nest();
}
