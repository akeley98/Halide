#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// Adversarial compute_inline x fuse (companion to compute_inline_split_nonpure).
// Same idea: a non-pure inline Func is realized at its innermost use, so a fuse
// on its pure stage takes effect there. The stencil g = f(x,y) + f(x+1,y) makes
// f needed over a 2-wide x range at each g point, so the fused loop x.xy does
// NOT collapse -- it survives as a single fused loop (a fuse of a 2x1 region).
// The unscheduled update keeps its own x (the 2-wide range) and collapses y.
// f.update(0).unscheduled() suppresses the "update not scheduled" warning.
int main() {
    Var x("x"), y("y"), xy("xy");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), out("out");
    f(x, y) = in(x, y);
    f(x, y) += in(x, y);              // update -> non-pure
    g(x, y) = f(x, y) + f(x + 1, y);  // stencil -> f realized over a 2-wide x range
    out(x, y) = g(x, y);
    g.compute_root();
    f.compute_inline().fuse(x, y, xy);  // inline, but fuse applies to the realized pure stage
    f.update(0).unscheduled();
    // Declared elision: the pure stage's fused xy loop survives (2x1 region);
    // the update keeps its x (2-wide) and collapses y.
    micro_halide_collapses(f.update(), {y});
    out.print_loop_nest();
}
