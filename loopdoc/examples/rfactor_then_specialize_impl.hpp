#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Family: rfactor BEFORE specialize, then schedule the rfactor output
// (loopdoc.md §1/§12/§15 — scheduling-state aliasing). Because rfactor runs on
// the BASE update BEFORE the specialize fork, the fork copies the *already
// rfactor'd* definition, so BOTH the specialized branch and the fallback read
// the SAME intermediate `g_intm`. Scheduling `g_intm` (here compute_root + a
// tile of its pure stage) is therefore shared by both branches — the scheduling
// state of the one intermediate propagates to every branch that aliases it.
// The specialize(cond).split(x) makes the two branches structurally distinct
// (so they don't merge) while both still consume the shared intm.
//
// tile_reduction selects the family member:
//   false — rfactor the whole reduction var r.y into u.
//   true  — first split the reduction (split r.x into rxo/rxi) and rfactor only
//           the INNER half rxi (the tiled-histogram shape). This exercises
//           rfactor of an RVar produced by split.
//
// NOTE: only the tile_reduction=false member has a committed .cpp. The
// tile_reduction=true member (rfactor_then_specialize_tiled) is HELD: it hits
// the deferred RVar-split gap (progress.txt [open] rfactor -- micro's RVar
// tracking is not updated by split, so rfactor's merge-drop cannot recognise the
// split halves and emits an extra reduction loop). It is verified RED against
// real Halide and is ready to add once RVar-splitting is tackled. Its failure is
// about RVar tracking, not the scheduling-state aliasing this family targets.
[[nodiscard]] int main_impl(bool tile_reduction) {
    Var x("x"), u("u"), xo("xo"), xi("xi"), ixo("ixo"), iuo("iuo"), ixi("ixi"), iui("iui");
    RVar rxo("rxo"), rxi("rxi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func g("g");
    RDom r(0, 16, 0, 8, "r");
    g(x) = 0;
    g(x) += cast<int32_t>(in(r.x, r.y));

    Func intm;
    if (tile_reduction) {
        g.update(0).split(r.x, rxo, rxi, 4);   // tile the reduction
        intm = g.update(0).rfactor(rxi, u);    // rfactor the inner half
    } else {
        intm = g.update(0).rfactor(r.y, u);    // rfactor the whole r.y
    }
    // Schedule the rfactor output; it is aliased by both specialize branches.
    intm.compute_root().tile(x, u, ixo, iuo, ixi, iui, 4, 4);

    Param<bool> cond;
    g.update(0).specialize(cond).split(x, xo, xi, 4);  // distinct branches, both read intm
    g.print_loop_nest();
    return 0;
}
