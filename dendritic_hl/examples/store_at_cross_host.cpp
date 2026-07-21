#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// store_at on a DIFFERENT host than compute_at.
//
// g is computed inside f's x loop (g.compute_at(f, x)), but its storage is
// allocated in the output's y loop (g.store_at(output, y)) -- a different host
// Func from the compute host. The `store g:` node therefore lands at the
// output's y loop and wraps f's entire realization (produce f ... consume f),
// while g's produce/consume stays down inside f's x loop.
//
// (g is read by f only at a single y per f-pixel, so g's y loop elides; its x
// loop survives. -- section 7.)

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func f("f");
    f(x, y) = g(x, y) + g(x + 1, y);

    Func output("output");
    output(x, y) = f(x, y) + f(x, y + 1);

    f.compute_at(output, y);
    g.store_at(output, y).compute_at(f, x); // store host (output) != compute host (f)

    micro_halide_collapses(g, {y});

    output.print_loop_nest();
}
