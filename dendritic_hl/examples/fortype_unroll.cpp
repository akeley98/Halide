#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `unroll(v)` sets an existing dimension's type to `unrolled` in place
// (loopdoc.md §17). Like `vectorize`, print_loop_nest shows the loop literally
// (no unroll lowering pass runs), so the observable is the `unrolled` token on
// the inner loop of `for y: unrolled x:`.

int main()
{
    Var x("x"), y("y");
    ImageParam in(type_of<int>(), 2, "in");
    Func f("f");
    f(x, y) = in(x, y);
    f.unroll(x);
    f.print_loop_nest();
}
