#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `reorder` made observable through a topological consequence.
//
// out.reorder(y, x, z) lists the dimensions innermost first, so out's
// dimension list becomes [y, x, z] and its loops print `for z: for x: for y:` --
// y is now the INNERMOST loop. `g.compute_at(out, y)` therefore lands at the
// deepest level, with no host loop remaining inside g's block, and g (needing
// only a single point per innermost iteration) emits no loops of its own:
//
//   produce out:
//     for z:
//       for x:
//         for y:
//           produce g:
//             g(...) = ...
//           consume g:
//             out(...) = ...
//
// Without the reorder (see reorder_baseline.cpp) the same schedule places g two
// levels out with a surviving `for x` inside its block. A pure-serial reorder is
// invisible on its own (the harness drops loop names and constant bounds); it
// only shows up by relocating a compute_at producer like this.

int main()
{
    Var x("x"), y("y"), z("z");

    ImageParam in(type_of<uint8_t>(), 3, "in");

    Func g("g");
    g(x, y, z) = in(x, y, z);

    Func out("out");
    out(x, y, z) = g(x, y, z);

    out.reorder(y, x, z); // y innermost, then x, then z
    g.compute_at(out, y);
    micro_halide_collapses(g, {x, y, z}); // single point per innermost iteration

    out.print_loop_nest();
}
