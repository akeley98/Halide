#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§12): the factored reduction reads a real Func producer, not just
// an ImageParam. The intermediate inherits that read, so g must precede the
// intermediate (and f) in realization order; the merge reads only the
// intermediate. This checks the producer-wiring half of rfactor (the four core
// examples all reduce over an ImageParam, which contributes no Func edge):
//
//   produce g:                 # g compute_root, outermost
//     for y: for x: g
//   consume g:
//     produce f_intm:          # reads g; reduces over r.x
//       for u: for x:
//       for x: for u: for r(=r.x):
//     consume f_intm:
//       produce f:
//         for x:                # f init
//         for x: for r(=r.y):    # merge over r.y, reading f_intm

int main()
{
    Var x("x"), y("y"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y) * 2;

    Func f("f");
    RDom r(0, 8, 0, 8, "r");
    f(x) = 0;
    f(x) += g(r.x, r.y);

    g.compute_root();
    Func intm = f.update(0).rfactor(r.y, u);
    intm.compute_root();

    f.print_loop_nest();
}
