#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `fuse` removes one loop. f's dimension list [x, y] (loops `for y: for x:`)
// becomes [xy] under fuse(x, y, xy): a single loop `for xy:` iterating over the
// product of the two extents. One `for` where there were two.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);

    Var xy("xy");
    f.fuse(x, y, xy);

    f.print_loop_nest();
}
