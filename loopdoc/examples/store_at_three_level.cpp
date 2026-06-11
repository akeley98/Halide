#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Store level several loops outside the compute level.
//
// In a 3-D pipeline, g is stored at the output's outermost loop (c) but
// computed at its innermost loop (x). The `store g:` node opens at the c level
// and must wrap BOTH host loops between it and the compute level (the y loop
// and the x loop) before g's produce/consume appears. This checks that the
// store node wraps every host loop between the store and compute levels, not
// just one.
//
// (g is read pointwise in y and c, so those loops of g elide; its x loop
// survives. -- section 7.)

int main()
{
    Var x("x"), y("y"), c("c");

    ImageParam in(type_of<uint8_t>(), 3, "in");

    Func g("g");
    g(x, y, c) = in(x, y, c);

    Func output("output");
    output(x, y, c) = g(x, y, c) + g(x + 1, y, c);

    g.store_at(output, c).compute_at(output, x);

    micro_halide_collapses(g, {y, c});

    output.print_loop_nest();
}
