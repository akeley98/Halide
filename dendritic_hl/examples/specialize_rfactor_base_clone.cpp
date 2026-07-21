#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Anti-overfit companion to specialize_rfactor_branch.cpp (loopdoc.md §12, §15).
// Two things it does differently, to force "route rfactor by WHICH handle" rather
// than a hack that always factors the branch:
//
//  1. REVERSED convention: the specialization is forked FIRST (a naive copy),
//     then rfactor is called on a fresh BASE handle -- so the FALLBACK/base is
//     factored (reads the intermediate, one merge loop over r.y) while the
//     SPECIALIZED branch keeps the original naive reduction (two loops over r.x,
//     r.y, reading `in`). Opposite of specialize_rfactor_branch.cpp.
//
//  2. clone_in AFTER rfactor+specialize: `g` is cloned for its consumer, so the
//     clone must carry a deep copy of BOTH the rfactor'd base RHS (reads intm)
//     AND the naive specialization branch -- testing that the copied state is not
//     lost or overfit to the specialize().rfactor() idiom. `g` is the clone's
//     sole source here, so `g` itself drops out; the clone (and the shared intm)
//     remain.
//
// Verified against real Halide:
//   produce g_intm:
//     for u: for x: g_intm(...)=...               # intm init
//     for x: for u: for r: g_intm(...)=...        # intm partial (reduces r.x)
//   consume g_intm:
//     produce g_clone_in_out:
//       for x: g_clone_in_out(...)=...            # clone init
//       for x: for r: for r: g_clone_in_out(...)  # SPECIALIZED branch: naive (r.x, r.y)
//       for x: for r: g_clone_in_out(...)         # fallback: rfactor'd merge over r.y
//     consume g_clone_in_out:
//       produce out: for x: out(...)=...
int main() {
    Var x("x"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func g("g");
    RDom r(0, 10, 0, 10, "r");
    g(x) = 0;
    g(x) += cast<int32_t>(in(r.x, r.y));
    Param<bool> cond;
    g.update(0).specialize(cond);              // fork the naive branch first
    Func intm = g.update(0).rfactor(r.y, u);   // rfactor the BASE (fallback) only
    intm.compute_root();
    Func out("out");
    out(x) = g(x);
    Func gc = g.clone_in(out);                 // clone the rfactor'd+specialized g
    gc.compute_root();
    out.print_loop_nest();
}
