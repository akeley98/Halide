#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// store_at vs compute_at at different levels of the same host.
//
// g's storage is allocated in the output's outer (y) loop, but g is computed in
// the output's inner (x) loop. Because the store level (y) differs from the
// compute level (x), a `store g:` node appears at the y level, wrapping the x
// loop in which g is produced/consumed. The produce/consume of g and all its
// loops are exactly where compute_at(output, x) alone would place them; the
// only addition is the `store g:` line.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + g(x, y + 1) + g(x + 1, y + 1);

    g.compute_at(output, x).store_at(output, y);

    output.print_loop_nest();
}
