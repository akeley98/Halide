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
// A per-stage split makes a Var stage-specific. f's update stage is split
// (x -> xo, xi), so the update stage's loops are [xi, xo, y] -- it no longer has
// an x loop, while the pure stage still does. p is read by BOTH stages, so
// p.compute_at(f, x) is illegal: x is not a loop of the (split) update stage, so
// it cannot enclose that stage's use of p. (Split vars are stage-specific by
// name, exactly like RVars; the legal sites here are y and root.)

int main()
{
    Var x("x"), y("y"), xo("xo"), xi("xi");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = in(x, 0);

    Func f("f");
    f(x, y) = p(x);
    f(x, y) += p(x);
    f.update(0).split(x, xo, xi, 8); // x exists in the pure stage but not the split update stage

    p.compute_at(f, x); // illegal: the update stage has no x loop (it was split)

    f.print_loop_nest();
}
