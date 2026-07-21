#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (loopdoc.md section 3, "A stage's loops"): the reduction-loop
// order follows the RDom's DECLARATION order (first-declared r.x innermost),
// NOT the textual order the RVars happen to appear in the update expression.
//
// Every other multi-dim-RDom example writes the RVars in declaration order
// (e.g. in(r.x, r.y)), so appearance order == declaration order and an
// implementation that ordered by appearance would pass them all. Here r.y
// appears textually FIRST on the RHS (q(r.y) + p(r.x)), yet r.x must still be
// the innermost reduction loop. We make that order observable by computing p
// (read over r.x) at f's OUTER reduction loop r.y: if r.x is innermost, p lands
// just inside r.y and spans the whole r.x range:
//
//   produce f:
//     for x:                     # f init
//     for x:
//       for r(=r.y):              # OUTER reduction (declared second)
//         produce p:
//           for x:                #   p spans the r.x range it is read over
//             p(...) = ...
//         consume p:
//           for r(=r.x):          # INNER reduction (declared first)
//             f(...) = ...

int main()
{
    Var x("x");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p"), q("q");
    p(x) = in(x, 0);
    q(x) = in(0, x);

    Func f("f");
    RDom r(0, 4, 0, 4, "r");
    f(x) = 0;
    f(x) += q(r.y) + p(r.x); // r.y appears before r.x, but r.x is declared first

    p.compute_at(f, r.y); // legal only if r.y encloses the r.x loop p is read in
    f.print_loop_nest();
}
