#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp double_split_fuse_test. f and g are each split twice
// (x->xo,xi then xo->xoo,xoi) and then fuse(xoi, xi, t), and computed at h's y
// loop. g.compute_with(f, t) shares everything down to the fused t. Below t the
// only remaining loop is xoo (outer of the second split). The shared y comes
// from compute_at(h, y).
//
//   produce h:
//     for y:
//       produce f:
//         produce g:
//           for xoo:
//             for t: f
//             for t: g
//       consume f: consume g:
//         for x: h
int main() {
    try {
        Var x("x"), y("y"), xo("xo"), xi("xi"), xoo("xoo"), xoi("xoi"), t("t");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), h("h");
        f(x, y) = in(x, y);
        g(x, y) = in(x, y) + 1;
        h(x, y) = f(x, y) + g(x, y);
        f.split(x, xo, xi, 37);
        g.split(x, xo, xi, 37);
        f.split(xo, xoo, xoi, 5);
        g.split(xo, xoo, xoi, 5);
        f.fuse(xoi, xi, t);
        g.fuse(xoi, xi, t);
        f.compute_at(h, y);
        g.compute_at(h, y);
        g.compute_with(f, t);
        // f and g are computed per h-y, so their own y loop collapses to a point.
        micro_halide_collapses(f, {y});
        micro_halide_collapses(g, {y});
        h.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
