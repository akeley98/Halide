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
// transitivity x split: same chain as split_transitive_compute_at, but f is
// computed at h's INNER split var xi. xi exists, but it lives in `consume g`
// (the part of h's body that runs AFTER g is produced), so it does NOT enclose
// f's use, which is inside `produce g` under xo. f's legal sites on h are y and
// xo (the loops enclosing g), plus root -- not xi. This is the split-version of
// the transitive "site must enclose the use" rule.

int main()
{
    Var x("x"), y("y"), xo("xo"), xi("xi");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);
    Func g("g");
    g(x, y) = f(x, y) + f(x + 1, y);
    Func h("h");
    h(x, y) = g(x, y);

    h.split(x, xo, xi, 8);
    g.compute_at(h, xo);
    f.compute_at(h, xi); // illegal: xi is in consume g, does not enclose f's use

    h.print_loop_nest();
}
