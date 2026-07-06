#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// Adversarial compute_inline x split. compute_inline() sets the compute level to
// inlined, but a NON-PURE Func (it has an update) is still realized -- at its
// innermost use (loopdoc.md section 11). So "inline" does NOT mean "no loops":
// the split recorded on f's pure stage DOES take effect on those realized loops.
// Here f is realized at g's (x,y); its pure stage's x is split into xo/xi (xo
// elides to a point, xi survives), and its unscheduled update collapses to a
// point. f.update(0).unscheduled() suppresses the "update not scheduled" warning
// (which would otherwise break the canonicalizer). Contrast a PURE inline Func,
// which vanishes entirely regardless of any split.
int main() {
    Var x("x"), y("y"), xo("xo"), xi("xi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), out("out");
    f(x, y) = in(x, y);
    f(x, y) += in(x, y);            // update -> f is non-pure (still realized when inline)
    g(x, y) = f(x, y);
    out(x, y) = g(x, y);
    g.compute_root();
    f.compute_inline().split(x, xo, xi, 4);   // inline compute level, but split still applies
    f.update(0).unscheduled();
    // Declared elision (bounds out of scope): realized at a point, f's pure stage
    // keeps only the split inner loop (xo, y -> points); the update is a point.
    micro_halide_collapses(f, {xo, y});
    micro_halide_collapses(f.update(), {x, y});
    out.print_loop_nest();
}
