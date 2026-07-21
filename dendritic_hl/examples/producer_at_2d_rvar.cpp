#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL: inject a producer at a SPECIFIC reduction loop of a 2-D RDom.
// This pins down the RVar nesting order, which a bare 2-D reduction
// (update_2d_rdom) cannot, because the harness drops the loop names.
//
// The update stage's reduction loops are `for r.y: for r.x:` (r.x, the
// first-declared, is innermost). p is read at p(r.x), so p.compute_at(f, r.y)
// injects p BETWEEN the two reduction loops -- inside r.y, outside r.x:
//
//   produce f:
//     for x:                  # stage 0
//       f(...) = ...
//     for x:                  # stage 1
//       for r.y:
//         produce p:
//           for x:            # p spans the r.x range it is read over
//             p(...) = ...
//         consume p:
//           for r.x:
//             f(...) = ...
//
// If r.x were innermost in name only but the impl placed it outermost, p would
// land in the wrong place; matching Halide confirms the order.

int main()
{
    Var x("x");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = in(x, 0) * 3;

    Func f("f");
    f(x) = 0;
    RDom r(0, 4, 0, 4, "r");
    f(x) += p(r.x) + in(x, r.y); // p read over r.x; r.y is the outer reduction

    p.compute_at(f, r.y); // inject between the reduction loops

    f.print_loop_nest();
}
