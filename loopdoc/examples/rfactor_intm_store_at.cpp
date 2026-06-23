#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// store_at of an rfactor intermediate (distinct store vs compute level). The
// intermediate is read only in f's merge stage, computed per (x, r.y) and stored
// once per x, so a `store f_intm:` node sits at the merge stage's x loop wrapping
// the produce/consume at the r.y loop. f's pure stage has an x loop matching the
// store level but computes no f_intm, so it gets NO store node.
//
//   produce f:
//     for x: f                       # pure stage: NO store node
//     for x:
//       store f_intm:                 # store node only in the merge stage
//         for r(=r.y):
//           produce f_intm:
//             f_intm                    # init (x,u collapsed)
//             for r(=r.x): f_intm
//           consume f_intm:
//             f

int main()
{
    Var x("x"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    RDom r(0, 8, 0, 8, "r");
    f(x) = 0;
    f(x) += in(r.x, r.y);

    Func intm = f.update(0).rfactor(r.y, u);
    intm.compute_at(f, r.y).store_at(f, x);
    micro_halide_collapses(intm, {x, u});
    micro_halide_collapses(intm.update(0), {x, u});

    f.print_loop_nest();
}
