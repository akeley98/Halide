#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp fuse_compute_at_test. Two independent fuse groups at
// different levels:
//   * q.compute_with(p, x): q fuses into p (both compute_root).
//   * g.compute_with(f, xo): f and g are split (x->xo,xi) and computed at h's y
//     loop; g fuses into f at the outer split var xo.
// h is computed at p's y loop. So the f/g group lives inside h's nest, which
// lives inside p's nest, which is fused with q.
int main() {
    try {
        Var x("x"), y("y"), xo("xo"), xi("xi");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), h("h"), p("p"), q("q"), r("r");
        f(x, y) = in(x, y);
        g(x, y) = in(x, y) + 1;
        h(x, y) = f(x, y) + g(x, y);
        p(x, y) = h(x, y) + 2;
        q(x, y) = x * y;
        r(x, y) = p(x, y) + q(x, y);
        f.compute_at(h, y);
        g.compute_at(h, y);
        h.compute_at(p, y);
        p.compute_root();
        q.compute_root();
        q.compute_with(p, x);
        f.split(x, xo, xi, 8);
        g.split(x, xo, xi, 8);
        g.compute_with(f, xo);
        // h computed per p-y; f,g computed per h-y: their own y loops collapse.
        micro_halide_collapses(h, {y});
        micro_halide_collapses(f, {y});
        micro_halide_collapses(g, {y});
        r.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
