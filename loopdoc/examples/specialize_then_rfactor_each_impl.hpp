#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Family: specialize FIRST, then rfactor EACH branch independently
// (loopdoc.md §1/§6/§12/§15 — distinct, non-aliased scheduling state + the
// realization-order tie-break). The specialized branch and the fallback each get
// their OWN intermediate Func, scheduled STRUCTURALLY differently:
//
//   Stage sp = g.update(0).specialize(cond);
//   Func intm_b = sp.rfactor(r.y, u);            // rfactor the BRANCH  -> intm_b
//   Func intm_f = g.update(0).rfactor(r.y, v);   // rfactor the FALLBACK -> intm_f
//
// Both intermediates are named "g_intm" (rfactor names every intermediate
// <orig>_intm), so the loop nest identifies them only by POSITION — their
// realization ORDER is the observable. Per §6, sibling producers with an equal
// name prefix are tie-broken by FIRST-VISITATION order, and within g's update
// stage the BASE definition's reads are visited before the specialization
// branch's reads. The base/fallback reads intm_f; the specialized branch reads
// intm_b; so intm_f is visited first and is realized OUTER, intm_b INNER. Their
// differing schedules then make that order observable after canonicalization.
//
// This is the scaffold for the §6 specialization-visitation clarification: micro
// must visit a stage's base-definition producers before its specialization
// branch's producers (progress.txt [open] "specialize x rfactor realization
// order"). It also confirms micro creates two DISTINCT intermediates (no
// aliasing) with the split applied to the correct one.
//
// tile: false -> intm_b gets a split on its pure stage, intm_f stays plain.
//       true  -> intm_b gets a tile (4 pure loops), intm_f gets a split (3).
[[nodiscard]] int main_impl(bool tile) {
    Var x("x"), u("u"), v("v"),
        bxo("bxo"), bxi("bxi"), buo("buo"), bui("bui"), fxo("fxo"), fxi("fxi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func g("g");
    RDom r(0, 10, 0, 10, "r");
    g(x) = 0;
    g(x) += cast<int32_t>(in(r.x, r.y));

    Param<bool> cond;
    Stage sp = g.update(0).specialize(cond);
    Func intm_b = sp.rfactor(r.y, u);            // branch's own intermediate
    Func intm_f = g.update(0).rfactor(r.y, v);   // fallback's own intermediate
    intm_b.compute_root();
    intm_f.compute_root();
    if (tile) {
        intm_b.tile(x, u, bxo, buo, bxi, bui, 4, 4);   // branch intm: 4 pure loops
        intm_f.split(x, fxo, fxi, 4);                  // fallback intm: 3 pure loops
    } else {
        intm_b.split(x, bxo, bxi, 4);                  // branch intm: 3 pure loops
        // intm_f stays plain: 2 pure loops
    }
    g.print_loop_nest();
    return 0;
}
