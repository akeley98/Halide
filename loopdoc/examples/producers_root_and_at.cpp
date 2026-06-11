#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Two producers of the same consumer, one at root and one compute_at.
//
// `g` is compute_root, so it is realized at the top level, before the output,
// in the root produce/consume chain. `h` is computed inside the output's y
// loop. The result combines the two nesting mechanisms:
//
//   produce g:
//     for y: for x: g(...) = ...
//   consume g:
//     produce output:
//       for y:
//         produce h: ...
//         consume h:
//           for x:
//             output(...) = ...
//
// A compute_root producer never nests inside the consumer's loops; a
// compute_at producer always does. The output's `produce` is inside `consume
// g`, and `h`'s block is inside the output's `for y`.

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

    g.compute_root();
    h.compute_at(output, y); // both of h's loops survive (y-stencil, full x row)

    output.print_loop_nest();
}
