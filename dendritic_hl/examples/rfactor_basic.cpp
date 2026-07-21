#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// rfactor, the basics. f is a 2-D sum reduction:
//   f(x)  = 0
//   f(x) += in(r.x, r.y)          # update stage 0, reduces over r.x (inner) and r.y
//
// f.update(0).rfactor(r.y, u) factors the reduction over r.y. It CREATES a new
// intermediate Func (auto-named "f_intm") and REWRITES f's update stage:
//
//   f_intm(x, u)  = 0             # new pure var u (was r.y); init dims [x, u]
//   f_intm(x, u) += in(r.x, u)    # partial sums, still reduces over r.x
//   f(x)  = 0
//   f(x) += f_intm(x, r.y)        # f's update now MERGES partials over r.y only
//
// With intm.compute_root() the intermediate is realized before f:
//
//   produce f_intm:
//     for u: for x:                 # init: dims innermost->outermost [x, u]
//     for x: for u: for r(=r.x):     # partial: original update order, r.y -> u
//   consume f_intm:
//     produce f:
//       for x:                       # f init
//       for x: for r(=r.y):          # f merge, reduces over r.y only

int main()
{
    Var x("x"), u("u");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    RDom r(0, 10, 0, 10, "r");
    f(x) = 0;
    f(x) += in(r.x, r.y);

    Func intm = f.update(0).rfactor(r.y, u);
    intm.compute_root();

    f.print_loop_nest();
}
