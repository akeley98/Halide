#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// The canonical update definition: a 1-D histogram (cf. ../apps/hist).
//
// hist has two stages, both inside one `produce hist`:
//   s0  hist(x) = 0                       -> pure stage, loops over x
//   s1  hist(clamp(in(r),0,255)) += 1     -> update stage; the LHS index is an
//                              expression, NOT a free Var, so this stage loops
//                              ONLY over the reduction variable r (a scatter).
//                              The clamp just bounds the write location (a
//                              scatter to an unbounded location is illegal); it
//                              is an Expr op and invisible in the loop nest:
//
//   produce hist:
//     for x:
//       hist(...) = ...
//     for r:
//       hist(...) = ...

int main()
{
    Var x("x");

    ImageParam in(type_of<int>(), 1, "in");

    Func hist("hist");
    hist(x) = 0;

    RDom r(0, 256, "r");
    hist(clamp(in(r), 0, 255)) += 1;

    hist.print_loop_nest();
}
