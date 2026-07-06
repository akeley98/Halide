#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// specialize on a Func DEEP in the pipeline (loopdoc.md section 15): the
// specialized Func need not be the output or its direct producer. Here the chain
// is a -> b -> c -> out, and the MIDDLE Func b is specialized. b's two branches
// live inside `produce b` (which sits under `consume ... produce c ...` above
// it), and b's own producer `a` (compute_at b.x) is injected into EACH branch's
// nest -- a mid-pipeline specialization interacts with both a producer below and
// a consumer above.
//
// Verified against real Halide:
//   produce b:
//     for y.yo: for y.yi: for x:  produce a: a(...) consume a: b(...)   <- branch
//     for y:             for x:  produce a: a(...) consume a: b(...)   <- fallback
//   consume b:
//     produce c: for y: for x: c(...)=...
//     consume c: produce out: for y: for x: out(...)=...
int main() {
    Var x("x"), y("y"), yo("yo"), yi("yi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func a("a"), b("b"), c("c"), out("out");
    a(x, y) = in(x, y);
    b(x, y) = a(x, y);
    c(x, y) = b(x, y);
    out(x, y) = c(x, y);
    c.compute_root();
    b.compute_root();
    Param<bool> cond;
    b.specialize(cond).split(y, yo, yi, 8);   // the specialized Func is mid-chain
    a.compute_at(b, x);                        // a injected into each of b's branches
    // a is at a single (x,y) point in every branch -> both its loops collapse.
    micro_halide_collapses(a, {x, y});
    out.print_loop_nest();
}
