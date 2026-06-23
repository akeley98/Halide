#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§12): preserve the FIRST-declared (innermost) reduction var r.x
// and lift r.y, the mirror image of rfactor_basic (which preserves r.y). This
// guards against an implementation that hardcodes "drop r.x": here r.x must
// SURVIVE in the merge and r.y must be lifted into the intermediate.
//
//   produce f_intm:
//     for u: for x:                 # init: new pure var u (was r.x)
//     for x: for r(=r.y): for u:     # partial: reduces over r.y; u where r.x was
//   consume f_intm:
//     produce f:
//       for x:                       # f init
//       for x: for r(=r.x):           # merge: reduces over r.x only

int main()
{
    Var x("x"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    RDom r(0, 8, 0, 8, "r");
    f(x) = 0;
    f(x) += in(r.x, r.y);

    Func intm = f.update(0).rfactor(r.x, u); // preserve the inner/first-declared RVar
    intm.compute_root();

    f.print_loop_nest();
}
