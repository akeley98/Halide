#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// rfactor x update(1) x TRANSITIVE compute_at. As rfactor_indirect_nested, but h
// is computed at the intermediate (h.compute_at(intm, u)) even though f_intm uses
// h ONLY through g. This is legal: h's use lives inside g, which is itself
// realized inside f_intm's u loop, so the u loop encloses h's use. h is injected
// just before g (a prefix), inside the update stage's u loop.
//
// The subtlety this pins down: the indirect pull is PER STAGE. The level
// (intm, u) names a u loop in BOTH of f_intm's stages, but f_intm's PURE stage
// reads neither g nor h -- so g is not realized there, and therefore h is NOT
// injected into the pure stage. Only the update stage (which actually uses g)
// pulls h in.
//
//   produce f_intm:
//     for u: for x:                 # pure stage: NOTHING injected (no g, no h)
//     for x: for u:
//       produce h:                   # h at f_intm.u, before g (used only via g)
//         for x: h
//       consume h:
//         produce g:                 # g at f_intm.u
//           for x: g
//         consume g:
//           for rb(=rb.x): f_intm

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
    h.compute_at(intm, u); // transitive: f_intm uses h only via g, both at intm.u
    micro_halide_collapses(g, {y});
    micro_halide_collapses(h, {y});

    f.print_loop_nest();
}
