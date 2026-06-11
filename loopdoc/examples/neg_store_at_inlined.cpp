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
// A store level only makes sense for a Func that is actually computed somewhere
// in the loop nest. Here g is given a store level but left at the default
// (inline) compute level -- it has no produce/consume to allocate storage
// around. Halide rejects it: "Func g is scheduled store_at(), but is inlined.
// Funcs that use store_at must also call compute_at."

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + g(x, y + 1) + g(x + 1, y + 1);

    g.store_at(output, y); // no compute_at/compute_root -> illegal

    output.print_loop_nest();
}
