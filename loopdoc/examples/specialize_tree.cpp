#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Specializations form a TREE (loopdoc.md section 15). Two ways to add one:
//   * calling specialize on a Func/Stage handle appends a SIBLING to that
//     handle's list -> a flat else-if chain (first match wins, in call order);
//   * calling specialize on a returned branch handle nests INSIDE that branch.
//
// Here (note specialize returns a Stage, not a Func):
//   Stage fa = f.specialize(cond_a);   // (1) child-of-root
//   f.specialize(cond_b);              // (2) SIBLING of cond_a (added to f)
//   fa.specialize(cond_c);             // (3) CHILD of cond_a (added to fa)
// gives the tree:
//   if cond_a:
//     if cond_c: [cond_a && cond_c]
//     else:      [cond_a && !cond_c]
//   else:
//     if cond_b: [!cond_a && cond_b]
//     else:      [!cond_a && !cond_b]
// So cond_b is only reached when cond_a is false; cond_c only when cond_a true.
//
// To make the four leaves visible, each carries a distinct transform (the
// cond_a subtree is tiled before cond_c is nested, so cond_c inherits the tile):
//   cond_a && cond_c -> tile + split (5 loops)
//   cond_a && !cond_c -> tile        (4 loops)
//   !cond_a && cond_b -> split       (3 loops)
//   !cond_a && !cond_b -> plain      (2 loops)
// print_loop_nest emits the four concatenated under one `produce f`, then-before-
// else / outer-first, so in that 5,4,3,2 order. Verified against real Halide:
//   produce f:
//     for yo: for xo: for yi: for xi.cx: for xi.cxi: f(...)   <- cond_a && cond_c
//     for yo: for xo: for yi: for xi:                f(...)   <- cond_a && !cond_c
//     for y:  for x.bx: for x.bxi:                   f(...)   <- !cond_a && cond_b
//     for y:  for x:                                 f(...)   <- !cond_a && !cond_b
//   consume f: produce out: for y: for x: out(...)=...
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi"),
        bx("bx"), bxi("bxi"), cx("cx"), cxi("cxi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> cond_a, cond_b, cond_c;
    Stage fa = f.specialize(cond_a);            // (1) branch handle (a Stage)
    fa.tile(x, y, xo, yo, xi, yi, 4, 4);        // cond_a subtree tiled (cond_c inherits)
    f.specialize(cond_b).split(x, bx, bxi, 8);  // (2) sibling of cond_a
    fa.specialize(cond_c).split(xi, cx, cxi, 2);// (3) child of cond_a
    out.print_loop_nest();
}
