#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// hoist_storage has NO effect on the printed loop nest.
//
// g is computed inside the output's x loop and its allocation is hoisted to the
// outermost level. hoist_storage_root moves where memory is allocated (out of
// the inner loop), but it does not trigger sliding-window reuse and does not add
// any node to print_loop_nest. This nest is byte-for-byte identical to plain
// g.compute_at(output, x) -- a regression guard that hoist_storage is invisible
// to the structure this experiment compares.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + g(x, y + 1) + g(x + 1, y + 1);

    g.compute_at(output, x).hoist_storage_root(); // hoist allocation out; no nest change

    output.print_loop_nest();
}
