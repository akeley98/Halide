#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§7 indirect consumer): h reads f BOTH directly and indirectly
// (through g). f.compute_at(h, y) injects a single produce f at h's y loop that
// serves both the direct use (in h's body) and the transitive one (inside g):
//
//   produce h:
//     for y:
//       produce f:              # one f realization at h.y
//         for x: f(...) = ...
//       consume f:
//         produce g:
//           for x: g(...) = ...
//         consume g:
//           for x: h(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);
    Func g("g");
    g(x, y) = f(x, y) + f(x + 1, y);
    Func h("h");
    h(x, y) = g(x, y) + f(x, y); // h reads g (which reads f) AND f directly

    g.compute_at(h, y);
    f.compute_at(h, y);

    micro_halide_collapses(g, {y}); // g spans x per h.y, one point in y
    micro_halide_collapses(f, {y});

    h.print_loop_nest();
}
