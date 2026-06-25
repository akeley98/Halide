#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp overlapping_updates_test. f and g each have a pure stage
// and a single update over the free Var x; the UPDATE stages are fused at x:
// g.update().compute_with(f.update(), x). f.update().unscheduled() tells Halide
// that stage need not be explicitly scheduled (load-bearing: keeps real Halide
// from emitting a Warning). The real test realizes Pipeline{f, g}; micro cannot,
// so out = f + g is the single output to print.
int main() {
    try {
        Var x("x");
        ImageParam in(type_of<uint8_t>(), 1, "in");
        Func f("f"), g("g"), out("out");
        f(x) = 0;
        f(x) += in(x);
        g(x) = 0;
        g(x) += in(x);
        out(x) = f(x) + g(x);
        f.compute_root();
        g.compute_root();
        g.update().compute_with(f.update(), x);
        f.update().unscheduled();
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
