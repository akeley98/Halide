#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Two producers of the same consumer, computed at DIFFERENT loop levels of it.
//
// `g` is computed at the output's inner (x) loop; `h` at the output's outer (y)
// loop. The two producers do NOT nest inside each other -- each is injected at
// its own loop level. Reading outermost-in:
//
//   produce output:
//     for y:
//       produce h: ...        # h injected at the y level
//       consume h:
//         for x:
//           produce g: ...    # g injected at the x level, inside consume h
//           consume g:
//             output(...) = ...
//
// So the producer at the OUTER level (h) appears first and wraps the producer
// at the inner level (g) inside its consume block. This shows that
// produce/consume nesting between sibling producers is governed by their
// compute levels, not by a single flat "consume" list.
//
// (Loop elision, section 7: g is read pointwise in y, so its y loop collapses
// and g keeps only its x loop. h is read with a y-stencil and over the full x
// row, so both of h's loops survive.)

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func h("h");
    h(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + h(x, y) + h(x, y + 1);

    g.compute_at(output, x); // inner loop
    h.compute_at(output, y); // outer loop

    micro_halide_collapses(g, {y}); // g read at a single y per output pixel

    output.print_loop_nest();
}
