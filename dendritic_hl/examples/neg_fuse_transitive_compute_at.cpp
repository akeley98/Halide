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
// transitivity x fuse: same chain as fuse_transitive_compute_at, but f is
// computed at h's x -- which no longer exists, because h.fuse(x, y, xy) consumed
// x into the fused var xy. f's legal sites on h are xy (the fused loop) and root;
// x is gone. (This is the fuse-version of the "site must be a current loop"
// rule, in a transitive setting.)

int main()
{
    Var x("x"), y("y"), xy("xy");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);
    Func g("g");
    g(x, y) = f(x, y) + f(x + 1, y);
    Func h("h");
    h(x, y) = g(x, y);

    h.fuse(x, y, xy);
    g.compute_at(h, xy);
    f.compute_at(h, x); // illegal: x was fused away on h

    h.print_loop_nest();
}
