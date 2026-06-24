#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// The "no single group parent" case. f's PURE stage fuses into g, and f's
// UPDATE stage fuses into h (a different Func). The fuse graph (child -> parent,
// per stage) is:
//
//     f.s0 --> g.s0        f.s1 --> h.s1
//
// so g and h are BOTH roots ("nobody's child") and f has TWO parents. There is
// no single "group parent": fusion is fundamentally a per-stage-pair relation.
// All three Funcs are nonetheless ONE fused group (connected through f), emitted
// as one interleaved sequence of stage nests wrapped by every member's
// produce/consume. Observed Halide structure:
//
//   produce h:
//     produce g:
//       produce f:
//         for y:            # g.s0 + f.s0 fused  (owned by g)
//           for x: g
//           for x: f
//         for y:            # h.s0  (unfused, its own nest)
//           for x: h
//         for fused.y:      # h.s1 + f.s1 fused  (owned by h)
//           for x: h
//           for x: f
//   consume h: consume g: consume f: produce out: ...
//
// Note f's two stages are NOT adjacent (h.s0 sits between them), yet both live
// under the single `produce f`.
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
        f.compute_root();
        g.compute_root();
        h.compute_root();
        f.compute_with(g, y);                       // f.s0 fuses into g.s0
        f.update().compute_with(h.update(), y);     // f.s1 fuses into h.s1
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
