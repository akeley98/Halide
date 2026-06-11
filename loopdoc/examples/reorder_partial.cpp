#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL: a PARTIAL reorder (fewer vars than dimensions).
//
// out has dimension list [x, y, z] (innermost first). out.reorder(z, x) names
// only two of the three dims: it permutes ONLY the slots z and x occupy (0 and
// 2), placing them innermost-first as requested -> slot 0 = z, slot 2 = x -- and
// leaves y untouched in slot 1. Result list [z, y, x], so x becomes the
// OUTERMOST loop. g.compute_at(out, x) then lands at the outermost level, and
// the residual out loops inside g's consume appear in the reordered order:
//
//   produce out:
//     for x:
//       produce g:
//         for z:
//           for y:
//             g(...) = ...
//       consume g:
//         for y:
//           for z:
//             out(...) = ...
//
// (g's own loops follow g's own dimension order [x, y, z] with x elided to a
// point; out's residual loops follow out's reordered list.)

int main()
{
    Var x("x"), y("y"), z("z");

    ImageParam in(type_of<uint8_t>(), 3, "in");

    Func g("g");
    g(x, y, z) = in(x, y, z);

    Func out("out");
    out(x, y, z) = g(x, y, z);

    out.reorder(z, x); // partial: only x and z named; y stays put
    g.compute_at(out, x);
    micro_halide_collapses(g, {x}); // single point in x, full y and z

    out.print_loop_nest();
}
