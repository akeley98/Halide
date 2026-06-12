#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§7 legality, transitive): h consumes f only INDIRECTLY, through a
// non-pure intermediate g that is itself computed at one of h's loops.
//
// g is computed at h's y loop, so g's whole realization (both stages) sits
// inside `for y` of h, and f -- read only by g -- is used inside g there. The
// loops enclosing f's use are therefore h.y, then g.y, g.x (NOT h.x, which lives
// in `consume g`). So f.compute_at(h, y) is legal: f is realized at h's y loop,
// just before g, covering g's reads:
//
//   produce h:
//     for y:
//       produce f:
//         for y:
//           for x:
//             f(...) = ...
//       consume f:
//         produce g:
//           for y:
//             for x:
//               g(...) = ...       # g stage 0
//           for y:
//             for x:
//               g(...) = ...       # g stage 1
//         consume g:
//           for x:
//             h(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);

    Func g("g");
    g(x, y) = f(x, y);
    g(x, y) += f(x + 1, y); // non-pure g; both stages read f

    Func h("h");
    h(x, y) = g(x, y) + g(x, y + 1);

    g.compute_at(h, y);
    f.compute_at(h, y); // legal: h.y encloses g, hence f's use

    h.print_loop_nest();
}
