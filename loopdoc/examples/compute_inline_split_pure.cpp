#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// KNOWN-FAILING, pending a harness change (committed failing on purpose so the
// upcoming change flips it cleanly). A PURE Func given compute_root + split and
// then compute_inline() vanishes (pure inline, §5) -- micro_halide prints exactly
// the default nest. But real Halide emits
//   "Warning: It is meaningless to split variable x of function f ... because f
//    is scheduled inline."
// to stderr (the split is now moot), and canonicalize.py raises a ParseError on
// that non-loop-nest line, so the harness reports "Canonicalizer failed" rather
// than comparing structure. Unlike the update "unscheduled" warning, this split
// warning has NO suppression API, so the whole "transform a PURE inline Func"
// category is untestable until the harness ignores `^Warning:` lines. See
// progress.txt (compute_inline milestone NOTES) for the suggested harness fix.
int main() {
    Var x("x"), y("y"), xo("xo"), xi("xi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), out("out");
    f(x, y) = in(x, y);
    g(x, y) = f(x, y);
    out(x, y) = g(x, y);
    g.compute_root();
    f.compute_root().split(x, xo, xi, 4);
    f.compute_inline();   // f vanishes; the recorded split is now meaningless (Halide warns)
    out.print_loop_nest();
}
