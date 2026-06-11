#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (positive complement of neg_compute_at_fused_away.cpp): the var a
// `fuse` PRODUCES is a legal compute_at site.
//
// out.fuse(x, y, xy) leaves out with the single dimension [xy]. Computing g at
// that fused loop is legal -- xy is a current dimension -- so g is injected just
// inside the one loop:
//
//   produce out:
//     for xy:
//       produce g:
//         g(...) = ...
//       consume g:
//         out(...) = ...
//
// g needs a single point per xy iteration, so it emits no loops of its own.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func out("out");
    out(x, y) = g(x, y);

    Var xy("xy");
    out.fuse(x, y, xy);
    g.compute_at(out, xy); // the fused var IS a legal site
    micro_halide_collapses(g, {x, y});

    out.print_loop_nest();
}
