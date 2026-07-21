#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// store_at at the SAME level as compute_at: no `store` node.
//
// Explicitly setting the store level equal to the compute level is a no-op as
// far as the printed loop nest is concerned: the `store g:` line is shown only
// when the two levels differ. This nest is identical to plain
// g.compute_at(output, y) -- a regression guard that the store node is
// suppressed when store == compute.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + g(x, y + 1) + g(x + 1, y + 1);

    g.store_at(output, y).compute_at(output, y); // store == compute -> no store node

    output.print_loop_nest();
}
