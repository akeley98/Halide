#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `parallel(v)` sets an existing dimension's loop TYPE in place -- no new loop
// (loopdoc.md §17). f(x,y) has loops `for y: for x:`; parallel(y) turns the
// outer loop into `parallel y:`. The harness keeps the type token, so the
// observable is the `parallel` on the outer loop (the inner stays `for`).

int main()
{
    Var x("x"), y("y");
    ImageParam in(type_of<int>(), 2, "in");
    Func f("f");
    f(x, y) = in(x, y);
    f.parallel(y);
    f.print_loop_nest();
}
