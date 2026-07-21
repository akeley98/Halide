#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `split` adds one loop. f's dimension list [x, y] (loops `for y: for x:`)
// becomes [xi, xo, y] under split(x, xo, xi, 8), printing `for y: for xo: for
// xi:` -- three loops where there were two. The inner loop's constant bound and
// the dotted var names are normalized away by the harness; the structural
// signal is purely the extra `for`.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);

    Var xo("xo"), xi("xi");
    f.split(x, xo, xi, 8);

    f.print_loop_nest();
}
