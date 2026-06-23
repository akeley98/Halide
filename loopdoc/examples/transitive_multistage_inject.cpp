#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§7 per-stage indirect pull): the indirect producer must be
// injected into EVERY stage that uses it, not just suppressed in one. f has two
// update stages, BOTH reading g; g reads h. h.compute_at(f, x) through g must
// land in BOTH update stages (each uses g at its x loop) but NOT in the pure
// stage (which reads nothing). This guards against an over-narrow fix that
// merely suppresses the pure stage:
//
//   produce f:
//     for y: for x: f                 # pure stage: nothing injected
//     for y: for x:                    # update 0
//       produce h: for x: h
//       consume h: produce g: g consume g: f
//     for y: for x:                    # update 1
//       produce h: for x: h
//       consume h: produce g: g consume g: f

int main()
{
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func h("h");
    h(x, y) = in(x, y);
    Func g("g");
    g(x, y) = h(x, y) + h(x + 1, y);

    Func f("f");
    f(x, y) = 0;
    f(x, y) += g(x, y);       // update 0 reads g (=> indirectly h)
    f(x, y) += g(x, y) * 2;   // update 1 reads g (=> indirectly h)

    g.compute_at(f, x);
    h.compute_at(f, x);       // transitive via g: lands in both update stages
    micro_halide_collapses(g, {x, y}); // g(x,y) is a single point at f's x loop
    micro_halide_collapses(h, {y});    // h spans x (2-tap), single y

    f.print_loop_nest();
}
