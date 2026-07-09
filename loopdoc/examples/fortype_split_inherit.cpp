#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A loop type rides on the dimension, so `split` copies it to BOTH halves
// (loopdoc.md §17 / §9). Here parallel(x) types x, then split(x, xo, xi, 4)
// replaces x with two loops -- and both inherit `parallel`. Expected:
// `parallel <outer>: parallel <inner>:`. (Compare fortype_vectorize_split.cpp,
// where the split came from the factor form and only ONE half was typed: that is
// the directive choosing a half, not `split` itself, which always copies.)

int main()
{
    Var x("x"), xo("xo"), xi("xi");
    ImageParam in(type_of<int>(), 1, "in");
    Func f("f");
    f(x) = in(x);
    f.parallel(x);
    f.split(x, xo, xi, 4);
    f.print_loop_nest();
}
