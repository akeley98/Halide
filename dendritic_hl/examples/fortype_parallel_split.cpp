#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// The FACTOR form `parallel(v, n)` first splits `v` by `n` and then types the
// OUTER loop `parallel`, leaving the inner `for` (loopdoc.md §17) -- the mirror
// image of vectorize/unroll, which type the inner. So f(x) prints
// `parallel <outer>: for <inner>:` -- canonical [parallel, for], structurally
// distinct from fortype_vectorize_split.cpp's [for, vectorized].

int main()
{
    Var x("x");
    ImageParam in(type_of<int>(), 1, "in");
    Func f("f");
    f(x) = in(x);
    f.parallel(x, 8);
    f.print_loop_nest();
}
