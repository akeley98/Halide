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
[[nodiscard]] int main_impl(bool compute_at_child) {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        ImageParam in2(type_of<uint8_t>(), 2, "in");
        Func parent("parent"), child("child"), g("g"), out("out");

        parent(x, y) = in(x, y);
        g(x, y) = in2(x, y);
        child(x, y) = in(x, y) + g(x, y) + g(x + 1, y) + g(x, y + 1);
        out(x, y) = parent(x, y) + child(x, y);

        parent.compute_root();
        child.compute_root();
        out.compute_root();

        child.compute_with(parent, y);
        if (compute_at_child) {
            g.compute_at(child, y);
        }
        else {
            g.compute_at(parent, y);
        }

        g.store_root();

        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
