#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// NEGATIVE example (must fail in both Halide and micro_halide).
//
// Same transitive chain as transitive_compute_at_outer (h <- g <- f, with the
// non-pure g computed at h's y loop). f.compute_at(h, x) is ILLEGAL: h's x loop
// lives in `consume g` (it is where h reads g, AFTER g is produced), so it does
// NOT enclose f's use, which is inside `produce g` under h.y. f's legal sites
// are h.y, g.y, g.x and root -- the loops on the path to its use -- but not h.x.
// This is the subtle part of legality once a consumer is itself computed inside
// the host: only the host loops that enclose the intermediate are candidates.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);

    Func g("g");
    g(x, y) = f(x, y);
    g(x, y) += f(x + 1, y);

    Func h("h");
    h(x, y) = g(x, y) + g(x, y + 1);

    g.compute_at(h, y);
    f.compute_at(h, x); // illegal: h.x is in consume g, does not enclose f's use

    h.print_loop_nest();
}
