#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ILLEGAL (loopdoc.md section 15 Legality): the Func that CALLS compute_with must
// have no specializations. Here f is specialized and then fused into g with
// f.compute_with(g, y) -- real Halide rejects it ("Func f is scheduled to be
// computed with g, so it must not have any specializations."). A fused group is
// emitted as one shared, unconditional loop nest; per-branch variants of a member
// have no place in it.
//
// Note the restriction is on the CALLER (the member being fused in), not the
// target: g here may be specialized without tripping this check.
int main() {
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), out("out");
    f(x, y) = in(x, y);
    g(x, y) = in(x, y) + 1;
    out(x, y) = f(x, y) + g(x, y);
    f.compute_root();
    g.compute_root();
    Param<bool> cond;
    f.specialize(cond);           // caller of compute_with is specialized
    f.compute_with(g, y);         // illegal
    out.print_loop_nest();
}
