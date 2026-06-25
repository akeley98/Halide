#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp update_stage2_test ("two in a row"). Both of f's update
// stages fuse into the SAME parent stage g.update(0):
//   f.update(0).compute_with(g.update(0), y);
//   f.update(1).compute_with(g.update(0), y);
// out = f + g is the single output to print (real test realizes Pipeline{f,g}).
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), out("out");
        g(x, y) = in(x, y);
        g(x, y) = in(x, y) + g(x, y);
        g(x, y) = in(x, y) + g(x, y);
        f(x, y) = in(x, y);
        f(x, y) = in(x, y) + f(x, y);
        f(x, y) = in(x, y) + f(x, y);
        out(x, y) = f(x, y) + g(x, y);
        g.compute_root();
        f.compute_root();
        f.update(0).compute_with(g.update(0), y);
        f.update(1).compute_with(g.update(0), y);
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
