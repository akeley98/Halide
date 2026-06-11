#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// compute_at loop elision: x elided.
//
// f is computed at the output's innermost x loop. The output reads f only at a
// single x coordinate (dx = 0) but a 2-wide stencil in y (dy = 1). So per
// output pixel f is needed over a single column but two rows: f's x loop has
// extent 1 and Halide elides it, while its y loop (extent 2) survives.
//
// The `micro_halide_collapses` annotation declares this elision to micro_halide; under real
// Halide it is a no-op. See loopdoc.md for why elision is declared, not derived.

int main()
{
    Var x("x"), y("y");

    ImageParam input(type_of<uint8_t>(), 2, "input");

    Func f("f");
    f(x, y) = input(x, y) * 1.337f;

    const int dx = 0, dy = 1;
    Func output("output");
    output(x, y) = f(x, y) + f(x + dx, y) + f(x, y + dy) + f(x + dx, y + dy);

    f.compute_at(output, x);
    micro_halide_collapses(f, {x}); // f's x loop has extent 1 here and is elided

    output.print_loop_nest();
}
