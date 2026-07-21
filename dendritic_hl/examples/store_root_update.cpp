#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL: store_at / store_root interacting with a MULTI-STAGE Func. f has
// a pure stage and an update stage and is computed inside g; store_root() puts
// f's storage at the outermost level. The `store f:` node must wrap f's WHOLE
// multi-stage produce (both stages), confirming the store node is per-Func and
// not per-stage:
//
//   store f:
//     produce g:
//       for y:
//         produce f:
//           for x:            # f stage 0
//             f(...) = ...
//           for x:            # f stage 1
//             for r:
//               f(...) = ...
//         consume f:
//           for x:
//             g(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x) = x;
    RDom r(0, 8, "r");
    f(x) += in(r, 0);

    Func g("g");
    g(x, y) = f(x);

    f.store_root().compute_at(g, y);

    g.print_loop_nest();
}
