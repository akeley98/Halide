#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// Cross-parent stage fusion when the group is NOT at root. As in
// compute_with_two_parents, f's pure stage fuses into g and f's update fuses
// into h -- but here all three are compute_at(out, y) (the SAME compute level,
// which is required: see neg_compute_with_level_mismatch). The whole group is
// injected as one loop nest inside out's y loop, and f is allocated at that
// shared level, enclosing BOTH of its fused stages, so f.s0 (in g's nest) writes
// the row that f.s1 (in h's nest) then updates. Each member's own y collapses
// (computed per out-y), leaving the shared x.
//
//   produce out:
//     for y:
//       produce f: produce g: produce h:
//         for x:  g ; f          # f.s0 + g.s0 fused
//         for x:  h              # h.s0
//         for x:  h ; f          # f.s1 + h.s1 fused
//       consume f: consume g: consume h:
//         for x:  out = f + g + h
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), h("h"), out("out");
        f(x, y) = in(x, y);
        f(x, y) += in(x, y);
        g(x, y) = in(x, y);
        h(x, y) = in(x, y);
        h(x, y) += in(x, y);
        out(x, y) = f(x, y) + g(x, y) + h(x, y);
        f.compute_at(out, y);
        g.compute_at(out, y);
        h.compute_at(out, y);                       // all three at the SAME level
        f.compute_with(g, x);                       // f.s0 fuses into g.s0
        f.update().compute_with(h.update(), x);     // f.s1 fuses into h.s1
        // Each member computed per out-y, so its own y collapses to a point.
        micro_halide_collapses(f, {y});
        micro_halide_collapses(f.update(), {y});
        micro_halide_collapses(g, {y});
        micro_halide_collapses(h, {y});
        micro_halide_collapses(h.update(), {y});
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
