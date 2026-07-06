#include "Halide.h"
#include <cstdio>
using namespace Halide;
// The Func that CALLS compute_with (func_2) must have no specializations.
int main() {
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Var x("x"), y("y");
    Func f("f"), g("g");
    f(x, y) = in(x, y);
    g(x, y) = in(x, y) + 1;
    Param<bool> cond;
    f.compute_root();
    g.compute_root();
    f.specialize(cond);        // the CALLER of compute_with is specialized
    f.compute_with(g, y);      // func_2 = f (has specialization) -> expect error
    Func out("out");
    out(x, y) = f(x, y) + g(x, y);
    out.print_loop_nest();
    return 0;
}
