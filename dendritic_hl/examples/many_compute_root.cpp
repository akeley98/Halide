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

    Func clamped = BoundaryConditions::repeat_edge(input);

    Func f1("f1");
    f1(x, y) = clamped(x, y) + clamped(x + 1, y + 1);

    Func f2("f2");
    f2(x, y) = f1(x, y) + f1(x + 1, y + 1);

    Func f3("f3");
    f3(x, y) = x + y;

    Func f4("f4");
    f4(x, y) = x + 3 * y;

    Func output("output");
    output(x, y) = f2(x, y) + f2(x + 1, y + 1) + f3(x, y) + f4(x, y) + f4(x, y + 1);

    f1.compute_root();
    f2.compute_root();
    f3.compute_root();
    f4.compute_at(output, y);

    output.print_loop_nest();
}
