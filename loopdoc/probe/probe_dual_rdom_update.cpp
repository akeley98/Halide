#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
#include <stdio.h>

// PROBE (real Halide only): is a SINGLE update stage allowed to reference two
// DIFFERENT RDoms? If Halide forbids it, the dual-RDom loop-order question is moot.
static void banner(const char *s){ fprintf(stderr, "\n===== %s =====\n", s); }
#define TRY(body) try { body } catch (const Halide::Error &e) { fprintf(stderr, "EXCEPTION: %s\n", e.what()); }

int main() {
    Var x("x");
    ImageParam in(type_of<int>(), 1, "in");

    banner("A: f(x) += in(x+r1) + in(x+r2), two separate 1-D RDoms in one update");
    TRY(
        RDom r1(0, 4, "r1");
        RDom r2(0, 5, "r2");
        Func f("f"); f(x) = 0;
        f(x) += in(x + r1) + in(x + r2);
        f.print_loop_nest();
    )

    banner("B: f(x) += in(r1) * in(r2)  (both RDoms as read indices)");
    TRY(
        RDom r1(0, 4, "r1");
        RDom r2(0, 5, "r2");
        Func f("f"); f(x) = 0;
        f(x) += in(r1) * in(r2);
        f.print_loop_nest();
    )

    banner("C: scatter with two RDoms  f(r1) += in(r2)");
    TRY(
        RDom r1(0, 4, "r1");
        RDom r2(0, 5, "r2");
        Func f("f"); f(x) = 0;
        f(r1) += in(r2);
        f.print_loop_nest();
    )

    banner("D: control -- single 2-D RDom (known legal)");
    TRY(
        RDom r(0, 4, 0, 5, "r");
        Func f("f"); f(x) = 0;
        f(x) += in(x + r.x) + in(x + r.y);
        f.print_loop_nest();
    )
    return 0;
}
