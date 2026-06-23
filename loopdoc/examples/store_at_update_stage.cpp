#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// store_at x a producer used in only ONE stage of a multi-stage consumer.
// f's PURE stage does not read p; only its UPDATE stage does. p is computed at
// f's update x loop and stored at f's update y loop, so a `store p:` node appears
// -- but ONLY in the update stage, where p is actually computed. The pure stage,
// although it has a y loop matching the store level, gets no `store p:` node.
// (This is the store-node analogue of §7's per-stage injection rule: the store
// node attaches to the stage where the Func is produced, not to every stage with
// a loop matching the store level.)
//
//   produce f:
//     for y: for x: f               # pure stage: NO store node
//     for y:
//       store p:                     # store node only here (update stage)
//         for x:
//           produce p:
//             for y: p
//           consume p:
//             for r: f

int main()
{
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x, y) = in(x, y);

    Func f("f");
    RDom r(0, 8, "r");
    f(x, y) = 0;          // pure stage does NOT read p
    f(x, y) += p(x, r);   // update stage reads p

    p.compute_at(f, x).store_at(f, y);
    micro_halide_collapses(p, {x}); // p(x, r): single x at f's update x loop

    f.print_loop_nest();
}
