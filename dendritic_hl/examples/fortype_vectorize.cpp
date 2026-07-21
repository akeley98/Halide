#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `vectorize(v)` sets an existing dimension's type to `vectorized` in place
// (loopdoc.md §17) -- no split, no new loop. f(x,y) loops `for y: for x:`;
// vectorize(x) makes the inner loop `vectorized x:`. print_loop_nest shows the
// loop literally (it does not run the vectorize lowering pass), so the observable
// is just the `vectorized` token on the inner loop.

int main()
{
    Var x("x"), y("y");
    ImageParam in(type_of<int>(), 2, "in");
    Func f("f");
    f(x, y) = in(x, y);
    f.vectorize(x);
    f.print_loop_nest();
}
