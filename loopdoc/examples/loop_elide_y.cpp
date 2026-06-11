#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// compute_at loop elision: y elided.
//
// Mirror of loop_elide_x.cpp. Now the output reads f with a 2-wide stencil in x
// (dx = 1) but a single y coordinate (dy = 0), so f's y loop collapses to
// extent 1 and is elided, while its x loop survives.

int main()
{
    Var x("x"), y("y");

    ImageParam input(type_of<uint8_t>(), 2, "input");

    Func f("f");
    f(x, y) = input(x, y) * 1.337f;

    const int dx = 1, dy = 0;
    Func output("output");
    output(x, y) = f(x, y) + f(x + dx, y) + f(x, y + dy) + f(x + dx, y + dy);

    f.compute_at(output, x);
    micro_halide_collapses(f, {y}); // f's y loop has extent 1 here and is elided

    output.print_loop_nest();
}
