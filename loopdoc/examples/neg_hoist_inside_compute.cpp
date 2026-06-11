#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// NEGATIVE example (must fail in both Halide and micro_halide).
//
// The hoist-storage level must ENCLOSE the store level (which encloses the
// compute level): an allocation cannot live inside the loop whose iterations are
// meant to reuse it. Here g is computed at the output's outer (y) loop but its
// storage is hoisted to the inner (x) loop -- inside the compute level. Halide
// rejects it with an "invalid location" error.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + g(x, y + 1) + g(x + 1, y + 1);

    g.compute_at(output, y).hoist_storage(output, x); // hoist (x) inside compute (y) -> illegal

    output.print_loop_nest();
}
