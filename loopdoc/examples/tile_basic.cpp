#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `tile` is two splits plus a reorder. tile(x, y, xo, yo, xi, yi, 8, 8) is
// exactly split(x,xo,xi,8); split(y,yo,yi,8); reorder(xi,yi,xo,yo). The
// dimension list becomes [xi, yi, xo, yo] (innermost first), printing the tiled
// traversal `for yo: for xo: for yi: for xi:` -- four loops where there were
// two.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);

    Var xo("xo"), yo("yo"), xi("xi"), yi("yi");
    f.tile(x, y, xo, yo, xi, yi, 8, 8);

    f.print_loop_nest();
}
