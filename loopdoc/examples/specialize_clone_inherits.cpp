#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// clone_in DEEP-COPIES the whole function state, including specializations
// (loopdoc.md sections 13, 15). A clone is an independent copy of the wrapped
// Func's definition (all stages) AND its schedule, so it starts with a COPY of
// the wrapped Func's specialization list (each condition + its forked schedule).
// Contrast in(), which builds a FRESH pointwise wrapper (wrapper(args) =
// f(args)) with no specializations of its own.
//
// Here f is specialized (tiled branch + plain fallback). `g` reads f; `keep`
// also reads f (so the original f stays in the pipeline). `fc = f.clone_in(g)`
// gives g an independent clone. Both f AND the clone print with TWO branches --
// the clone inherited f's specialization. Verified against real Halide:
//   produce f_clone_in_g:
//     for y.yo: for x.xo: for y.yi: for x.xi: f_clone_in_g(...)=...   <- clone branch
//     for y:    for x:                          f_clone_in_g(...)=...  <- clone fallback
//   consume f_clone_in_g:
//     produce f:
//       for y.yo: for x.xo: for y.yi: for x.xi: f(...)=...            <- original branch
//       for y:    for x:                          f(...)=...           <- original fallback
//     consume f: produce out: for y: for x: out(...)=...
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), keep("keep"), out("out");
    f(x, y) = in(x, y);
    f.compute_root();
    Param<bool> cond;
    f.specialize(cond).tile(x, y, xo, yo, xi, yi, 4, 4);   // f specialized
    g(x, y) = f(x, y);
    keep(x, y) = f(x, y);                                  // keeps original f alive
    Func fc = f.clone_in(g);                               // clone for consumer g
    fc.compute_root();
    out(x, y) = g(x, y) + keep(x, y);
    out.print_loop_nest();
}
