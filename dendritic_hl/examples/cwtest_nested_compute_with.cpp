#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp nested_compute_with_test. Two nested fuse groups:
//   * f2.compute_with(f1, y), with input.compute_at(f1, y): f1/f2 fused at y,
//     and f1, f2 themselves compute_at(g1, y).
//   * g2.compute_with(g1, x): g1/g2 fused at x (compute_root).
// So the f1/f2 group lives inside the g1/g2 group's nest. The real test realizes
// a Pipeline {g1, g2}; micro cannot, so we add out = g1 + g2 as the single
// output to print.
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func input("input"), f1("f1"), f2("f2"), g1("g1"), g2("g2"), out("out");
        input(x, y) = in(x, y);
        f1(x, y) = input(x, y) + 20;
        f2(x, y) = input(x, y) * input(x, y);
        g1(x, y) = f1(x, y) + x + y;
        g2(x, y) = f1(x, y) * f2(x, y);
        out(x, y) = g1(x, y) + g2(x, y);
        g1.compute_root();   // g1, g2 were Pipeline outputs (implicitly root)
        g2.compute_root();
        input.compute_at(f1, y);
        f2.compute_with(f1, y);
        f1.compute_at(g1, y);
        f2.compute_at(g1, y);
        g2.compute_with(g1, x);
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
