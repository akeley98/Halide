#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A loop type stays attached to its dimension when `reorder` moves it, so a
// reorder of a TYPED loop is directly observable (loopdoc.md §17 / §9) -- unlike
// reordering plain serial loops, which is invisible once names/bounds are
// normalized. f(x,y) with vectorize(x) is `for y: vectorized x:` (canonical
// [for, vectorized]). reorder(y, x) makes x the outer loop, carrying its type:
// `vectorized x: for y:` (canonical [vectorized, for]) -- a different structure.

int main()
{
    Var x("x"), y("y");
    ImageParam in(type_of<int>(), 2, "in");
    Func f("f");
    f(x, y) = in(x, y);
    f.vectorize(x);
    f.reorder(y, x);        // x becomes the outer loop; its `vectorized` moves with it
    f.print_loop_nest();
}
