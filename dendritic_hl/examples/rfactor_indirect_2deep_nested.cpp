#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// rfactor x update(1) x TWO-deep indirect use. The chain is f_intm -> g -> k -> h
// (f's update(1) reduces over g; g reads k; k reads h), all nested by compute_at:
// g at f_intm.u, k inside g, h inside k. Confirms the indirect-use recursion
// works two intermediates deep, inside the rfactor intermediate's reducing stage,
// and that nothing is injected into f_intm's pure stage.
//
//   produce f_intm:
//     for u: for x:                 # pure stage: nothing injected
//     for x: for u:
//       produce g:                   # g at f_intm.u
//         for x:
//           produce k:               # k inside g.x
//             for y:
//               produce h:           # h inside k.x
//                 for x: h
//               consume h: k
//           consume k: g
//       consume g:
//         for rb(=rb.x): f_intm

int main()
{
    Var x("x"), y("y"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func h("h");
    h(x, y) = in(x, y);
    Func k("k");
    k(x, y) = h(x, y) + h(x + 1, y);   // k reads h
    Func g("g");
    g(x, y) = k(x, y) + k(x, y + 1);   // g reads k

    Func f("f");
    RDom ra(0, 8, "ra");
    RDom rb(0, 8, 0, 8, "rb");
    f(x) = 0;
    f(x) += in(x, ra);
    f(x) += g(rb.x, rb.y);             // update 1 reads g (=> k => h)

    f.update(0).reorder(ra, x);
    Func intm = f.update(1).rfactor(rb.y, u);
    intm.compute_root();
    g.compute_at(intm, u);
    k.compute_at(g, x);
    h.compute_at(k, x);
    micro_halide_collapses(g, {y}); // g(rb.x, u): single point in y
    micro_halide_collapses(k, {x}); // k spans y (2-tap in y), single x at g's x
    micro_halide_collapses(h, {y}); // h spans x (2-tap in x), single y at k's y

    f.print_loop_nest();
}
