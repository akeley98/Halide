#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

int main()
{
    Var x("x"), y("y");

    ImageParam input(type_of<uint8_t>(), 2, "input");

    Func f("f");
    f(x, y) = input(x, y) * 1.337f;

    const int dx = 1, dy = 1;
    Func output("output");
    output(x, y) = f(x, y) + f(x + dx, y) + f(x, y + dy) + f(x + dx, y + dy);

    // x and y loops of f will be elided if dx = 0 or dy = 0 respectively.
    f.compute_at(output, x);

    output.print_loop_nest();
}
