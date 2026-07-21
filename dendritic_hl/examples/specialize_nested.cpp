#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Nested specialize (loopdoc.md section 15): a specialization may itself be
// specialized, producing a nested if/else. Flattened, the branches print in the
// order the nested-if visits them: (c1 & c2), (c1 & !c2), then the fallback.
//
// c1's branch tiles (inheriting nothing else); c2 (nested in c1) adds a split of
// the tile inner x. So the three printed subtrees are:
//   tile+split (5 loops)  <- c1 & c2
//   tile       (4 loops)  <- c1 & !c2
//   plain      (2 loops)  <- fallback
// Two distinct conditions (cond1, cond2) so no specialization is re-fetched by a
// repeated Expr (loopdoc.md section 15 "Out of scope"). Verified vs real Halide.
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi"), xii("xii");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> cond1, cond2;
    f.specialize(cond1).tile(x, y, xo, yo, xi, yi, 4, 4).specialize(cond2).split(xi, xi, xii, 2);
    out.print_loop_nest();
}
