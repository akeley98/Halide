#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `fuse(inner, outer, fused)` collapses two loops into one, and the fused loop
// takes the INNER dimension's type -- the outer's type is dropped (loopdoc.md
// §17). Here x (inner) is `vectorized` and y (outer) is `parallel`; fusing them
// yields a single `vectorized` loop, proving the inner wins over the outer.
// Expected: one loop `vectorized <fused>:`.

int main()
{
    Var x("x"), y("y"), xy("xy");
    ImageParam in(type_of<int>(), 2, "in");
    Func f("f");
    f(x, y) = in(x, y);
    f.vectorize(x);         // inner dim -> vectorized
    f.parallel(y);          // outer dim -> parallel (will be dropped by fuse)
    f.fuse(x, y, xy);       // fused takes inner (x) -> vectorized
    f.print_loop_nest();
}
