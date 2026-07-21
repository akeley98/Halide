#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// rfactor preserving MORE THAN ONE reduction var, and reordering the
// intermediate's partial-reduction stage. A 3-D RDom reduces over r.x, r.y, r.z:
//
//   f(x)  = 0
//   f(x) += in(r.x, r.y, r.z)
//
// rfactor({{r.y, u}, {r.z, v}}) preserves r.y and r.z (-> new pure Vars u, v)
// and lifts r.x into the intermediate's reduction:
//
//   f_intm(x, u, v)  = 0                  # init dims innermost->out [x, u, v]
//   f_intm(x, u, v) += in(r.x, u, v)      # partial: reduces over r.x only
//   f(x)  = 0
//   f(x) += f_intm(x, r.y, r.z)           # merge: reduces over r.y, r.z
//
// intm.update().reorder(r.x, u, v) sets the partial stage's loop order
// (innermost->outermost: r.x, u, v, then x outermost). compute_root realizes
// the intermediate before f. Expected nest:
//
//   produce f_intm:
//     for v: for u: for x:                 # init
//     for x: for v: for u: for r(=r.x):    # partial
//   consume f_intm:
//     produce f:
//       for x:                             # f init
//       for x: for r(=r.z): for r(=r.y):    # merge over r.y, r.z
//
// (Factoring a reduction var that was first SPLIT -- the tiled-histogram
// pattern, e.g. split(r.x, rxo, rxi).rfactor({{rxo, u}}) -- additionally needs
// loop-splitting of RVars, which this document does not yet model; it is a
// deferred interaction, see progress.txt.)

int main()
{
    Var x("x"), u("u"), v("v");

    ImageParam in(type_of<uint8_t>(), 3, "in");

    Func f("f");
    RDom r(0, 8, 0, 8, 0, 8, "r");
    f(x) = 0;
    f(x) += in(r.x, r.y, r.z);

    Func intm = f.update(0).rfactor({{r.y, u}, {r.z, v}});
    intm.compute_root().update().reorder(r.x, u, v);

    f.print_loop_nest();
}
