#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// rfactor x update(1) x INDIRECT use. f's update(1) reduces over g, and g reads
// h -- so f's update(1) uses h only indirectly. rfactor(update(1)) moves the read
// of g (hence h) into the new intermediate f_intm. Here g is computed at f_intm's
// u loop and h is nested inside g, giving a transitive producer chain inside the
// intermediate's update stage. f_intm's PURE stage reads neither g nor h, so
// neither is injected there:
//
//   produce f_intm:
//     for u: for x:                 # pure stage: nothing injected
//     for x: for u:
//       produce g:                   # g at f_intm.u
//         for x:
//           produce h:               # h nested in g.x
//             for x: h
//           consume h: g
//       consume g:
//         for rb(=rb.x): f_intm

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
    f(x) += in(x, ra);          // update 0
    f(x) += g(rb.x, rb.y);      // update 1: reduces over g (=> indirectly h)

    f.update(0).reorder(ra, x); // identity; suppress the all-or-nothing warning
    Func intm = f.update(1).rfactor(rb.y, u);
    intm.compute_root();
    g.compute_at(intm, u);
    h.compute_at(g, x);
    micro_halide_collapses(g, {y}); // g indexed (rb.x, u): its y is a single point
    micro_halide_collapses(h, {y}); // h's y is a single point at g's y

    f.print_loop_nest();
}
