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
    Var x("x");

    ImageParam in(type_of<int>(), 1, "in");

    Func hist("hist");
    hist(x) = 0;

    RDom r(0, 256, "r");
    hist(clamp(in(r), 0, 255)) += 1;

    Func weird("weird");
    weird(x) = hist(x) * 10;

    weird.print_loop_nest();
}
