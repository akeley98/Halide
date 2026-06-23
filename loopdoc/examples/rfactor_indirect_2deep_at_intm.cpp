#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// rfactor x update(1) x TWO-deep indirect use, with the whole chain filed at the
// rfactor intermediate's u loop: g, k, AND h are all compute_at(intm, u). f_intm
// uses h only via g -> k, so h's legality and injection both run through TWO
// intermediates. The prefix injection order at u is h, then k, then g, then the
// reduction; none of them lands in f_intm's pure stage.
//
//   produce f_intm:
//     for u: for x:                 # pure stage: nothing injected
//     for x: for u:
//       produce h: for y: for x: h   # h at f_intm.u (used only via g,k)
//       consume h:
//         produce k: for y: for x: k
//         consume k:
//           produce g: for x: g
//           consume g:
//             for rb(=rb.x): f_intm

int main()
{
    Var x("x"), y("y"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func h("h");
    h(x, y) = in(x, y);
    Func k("k");
    k(x, y) = h(x, y) + h(x + 1, y);
    Func g("g");
    g(x, y) = k(x, y) + k(x, y + 1);

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
    k.compute_at(intm, u);
    h.compute_at(intm, u); // 2-deep transitive: reached only via g -> k
    micro_halide_collapses(g, {y});

    f.print_loop_nest();
}
