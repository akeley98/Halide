#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// The FACTOR form `vectorize(v, n)` first splits `v` by `n` and then types the
// INNER (width-n) loop `vectorized`, leaving the outer `for` (loopdoc.md §17).
// So f(x) prints `for <outer>: vectorized <inner>:` -- canonical [for, vectorized].
// Contrast fortype_parallel_split.cpp, where `parallel(x, 8)` types the OUTER
// loop instead: that asymmetry is the whole point of these two examples, and it
// is what makes a split's inner/outer order observable (§9).

int main()
{
    Var x("x");
    ImageParam in(type_of<int>(), 1, "in");
    Func f("f");
    f(x) = in(x);
    f.vectorize(x, 8);
    f.print_loop_nest();
}
