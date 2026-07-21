#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// specialize (loopdoc.md section 15): a Func's definition may carry conditional
// variants. print_loop_nest() prints EACH branch's loop nest concatenated as
// sibling subtrees under the SAME `produce f`, in declaration order, with the
// unspecialized fallback LAST -- and with NO if/else marker or condition text
// (PrintLoopNest has no IfThenElse visitor; the condition is invisible).
//
// Here f is compute_root; the specialization tiles it (4 loops), the fallback
// stays plain (2 loops). Verified against real Halide:
//   produce f:
//     for y.yo: for x.xo: for y.yi: for x.xi:  f(...)=...   <- specialization
//     for y:    for x:                          f(...)=...   <- fallback
//   consume f: produce out: for y: for x: out(...)=...
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> cond;
    f.specialize(cond).tile(x, y, xo, yo, xi, yi, 4, 4);
    out.print_loop_nest();
}
