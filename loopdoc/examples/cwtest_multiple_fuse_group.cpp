#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp multiple_fuse_group_test. Two separate fuse groups in
// one pipeline:
//   * f and g (each pure + one update) fuse: f.update(0).compute_with(g, y) and
//     f.compute_with(g, x). So f's pure stage fuses into g at x, and f's update
//     fuses into g at y.
//   * h (pure + RDom updates) fuses into p: p.fuse(x,y,t); h.fuse(x,y,t);
//     h.compute_with(p, t).
// (parallel/unscheduled/trace stripped.) q is the single output reading h and p.
int main() {
    try {
        Var x("x"), y("y"), t("t");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), h("h"), p("p"), q("q");
        f(x, y) = in(x, y);
        f(x, y) += in(x, y);
        g(x, y) = in(x, y) + 10;
        g(x, y) += in(x, y);
        h(x, y) = 0;
        RDom r(0, 39, 50, 77, "r");
        h(r.x, r.y) += in(r.x, r.y);   // original used -=; micro has no -= and the loop structure is identical
        h(r.x, r.y) += in(r.x, r.y);
        h(x, y) += f(x, y) + g(x, y);
        p(x, y) = x + 2;
        q(x, y) = h(x, y) + 2 + p(x, y);
        f.compute_root();
        g.compute_root();
        h.compute_root();
        p.compute_root();
        p.fuse(x, y, t);
        h.fuse(x, y, t);
        h.compute_with(p, t);
        h.update(0).unscheduled();
        h.update(1).unscheduled();
        h.update(2).unscheduled();
        f.update(0).compute_with(g, y);
        f.compute_with(g, x);
        q.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
