#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE. Two members fuse into EACH OTHER, flipping the child/parent direction
// between stages: f.s0 fuses into g.s0 (f is g's child), while g.s1 fuses into
// f.s1 (g is f's child). At the Func (member) level this demands both f-before-g
// and g-before-f, a cycle with no consistent member order. Halide rejects it up
// front: "Found cyclic dependencies between compute_with of f and g". (Distinct
// from neg_cwtest_crossing_edges*, which keep one direction but pin inconsistent
// stage indices.) Real Halide realizes a Pipeline{f, g}; micro cannot, so the
// single printed output is out = f + g.
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), out("out");
        f(x, y) = in(x, y);
        f(x, y) += in(x, y);                        // f.s1
        g(x, y) = in(x, y);
        g(x, y) += in(x, y);                        // g.s1
        out(x, y) = f(x, y) + g(x, y);
        f.compute_root();
        g.compute_root();
        f.compute_with(g, y);                       // f.s0 -> g.s0  (f child of g)
        g.update(0).compute_with(f.update(0), y);   // g.s1 -> f.s1  (g child of f)
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
