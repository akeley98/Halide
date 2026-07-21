#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// rfactor x update(1) x indirect use, with h pulled all the way OUT to root while
// g stays at the intermediate. h is a plain root producer (read by g); g is
// computed inside f_intm's u loop. Realization order: h, then f_intm (reads g
// reads h), then f.
//
//   produce h:                       # h root
//     for y: for x: h
//   consume h:
//     produce f_intm:
//       for u: for x:                 # pure stage
//       for x: for u:
//         produce g:                   # g at f_intm.u
//           for x: g
//         consume g:
//           for rb(=rb.x): f_intm
//     consume f_intm:
//       produce f: ...

int main()
{
    Var x("x"), y("y"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func h("h");
    h(x, y) = in(x, y);
    Func g("g");
    g(x, y) = h(x, y) + h(x + 1, y);

    Func f("f");
    RDom ra(0, 8, "ra");
    RDom rb(0, 8, 0, 8, "rb");
    f(x) = 0;
    f(x) += in(x, ra);
    f(x) += g(rb.x, rb.y);

    f.update(0).reorder(ra, x);
    Func intm = f.update(1).rfactor(rb.y, u);
    intm.compute_root();
    g.compute_at(intm, u);
    h.compute_root();
    micro_halide_collapses(g, {y});

    f.print_loop_nest();
}
