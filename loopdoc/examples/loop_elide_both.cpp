#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// compute_at loop elision: both loops elided.
//
// With dx = dy = 0 the output reads f at a single point per output pixel, so
// BOTH of f's loops collapse. f is realized with no loops at all -- just
// `produce f: f(...) = ...`. This is why compute_at at the innermost loop of a
// pointwise consumer behaves almost like inlining (lesson 8): the producer is
// recomputed at a single point each iteration.

int main()
{
    Var x("x"), y("y");

    ImageParam input(type_of<uint8_t>(), 2, "input");

    Func f("f");
    f(x, y) = input(x, y) * 1.337f;

    const int dx = 0, dy = 0;
    Func output("output");
    output(x, y) = f(x, y) + f(x + dx, y) + f(x, y + dy) + f(x + dx, y + dy);

    f.compute_at(output, x);
    micro_halide_collapses(f, {x, y}); // both loops have extent 1 here and are elided

    output.print_loop_nest();
}
