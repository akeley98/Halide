#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// Chained compute_with: a child may itself be the parent of a further fuse.
// `mid.compute_with(top, y)` then `bot.compute_with(mid, y)` ties all three into
// ONE fused group (compute_with's grouping is by connected component, not just
// "one parent + its direct children"). The GROUP PARENT -- the member whose
// loops actually own the shared nest -- is `top`, the root of the chain, even
// though bot's per-call parent is mid. So `top` is realized last / its produce
// is outermost / the group sits at top's level, regardless of the names (here
// top/mid/bot are reverse-alphabetical, to show the order is structural).
//
//   produce top:           # group parent (chain root), outermost
//     produce mid:
//       produce bot:
//         for fused.y:      # the shared loop belongs to top
//           for x: top      # body order: parent first, down the chain
//           for x: mid
//           for x: bot
//   consume top: consume mid: consume bot: produce out: ...
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func top("top"), mid("mid"), bot("bot"), out("out");
        top(x, y) = in(x, y);
        mid(x, y) = in(x, y) + 1;
        bot(x, y) = in(x, y) + 2;
        out(x, y) = top(x, y) + mid(x, y) + bot(x, y);
        top.compute_root();
        mid.compute_root();
        bot.compute_root();
        mid.compute_with(top, y);   // mid fused into top
        bot.compute_with(mid, y);   // bot fused into mid; group parent stays top
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
