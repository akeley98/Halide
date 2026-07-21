#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// The rfactor intermediate is an ordinary producer of f (f's merge stage reads
// it), so it can be compute_at'd at any loop of f that encloses that use.
// f's merge stage is `for x: for r(=r.y):`; the intermediate is used in its
// body. intm.compute_at(f, x) realizes it inside f's merge x loop -- between the
// x loop and the r.y loop. Only one x is needed there, so f_intm's x collapses,
// but u and r.x survive:
//
//   produce f:
//     for x:                       # f init
//     for x:
//       produce f_intm:
//         for u:                     # init: x collapsed
//           f_intm(...) = ...
//         for u: for r(=r.x):        # partial: x collapsed
//           f_intm(...) = ...
//       consume f_intm:
//         for r(=r.y):               # f merge body
//           f(...) = ...

int main()
{
    Var x("x"), u("u");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    RDom r(0, 10, 0, 10, "r");
    f(x) = 0;
    f(x) += in(r.x, r.y);

    Func intm = f.update(0).rfactor(r.y, u);
    intm.compute_at(f, x);
    micro_halide_collapses(intm, {x});           // init: only a point of x here
    micro_halide_collapses(intm.update(0), {x}); // partial: only a point of x here

    f.print_loop_nest();
}
