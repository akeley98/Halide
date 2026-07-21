#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// rfactor THROUGH a specialization handle (loopdoc.md sections 12, 15). Calling
// rfactor on the Stage returned by specialize edits ONLY that branch's
// definition: g.update(0).specialize(cond).rfactor(r.y, u) factors the
// SPECIALIZED branch (it reads the new intermediate; one merge reduction loop
// over r.y), while the fallback keeps the original naive reduction (two loops
// over r.x and r.y, reading `in` directly). The two branches thus run different
// but functionally-equivalent reduction algorithms; the intermediate is only
// referenced from the factored branch. This is first-class, tested Halide
// (test/correctness/rfactor.cpp).
//
// Verified against real Halide:
//   produce g_intm:
//     for u: for x: g_intm(...)=...              # intm init
//     for x: for u: for r: g_intm(...)=...       # intm partial (reduces r.x)
//   consume g_intm:
//     produce g:
//       for x: g(...)=...                        # g init
//       for x: for r: g(...)=...                 # SPECIALIZED branch: merge over r.y
//       for x: for r: for r: g(...)=...          # fallback: naive reduce r.x, r.y
//
// (History: this was the RED scaffold for the "specialize x rfactor" gap —
// micro's rfactor originally rewrote the BASE stage and emitted the mirror of
// the above. A micro-agent closed it by routing rfactor through the handle's
// definition — stage() — so it factors the branch here; loopdoc §12/§15. Now
// PASS. specialize_rfactor_base_clone.cpp is the reversed/base-factored guard.)
int main() {
    Var x("x"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func g("g");
    RDom r(0, 10, 0, 10, "r");
    g(x) = 0;
    g(x) += cast<int32_t>(in(r.x, r.y));
    Param<bool> cond;
    Func intm = g.update(0).specialize(cond).rfactor(r.y, u);  // factor the branch
    intm.compute_root();
    g.print_loop_nest();
}
