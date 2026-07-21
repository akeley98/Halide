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
// Both members are GREEN. The tile_reduction=true member exercises rfactor of a
// split-produced RVar; it works now that split/fuse preserve DimData::is_rvar
// (human fix a09d33c6d) plus the iuo collapse annotation below.
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
    if (tile_reduction) {
        // In the tiled member u = rxi has extent 4 (= the split factor), so the
        // u-outer tile loop iuo has extent 1 and elides (§7). In the non-tiled
        // member u = r.y has extent 8, so iuo survives -- no collapse there.
        micro_halide_collapses(intm, {iuo});
    }

    Param<bool> cond;
    g.update(0).specialize(cond).split(x, xo, xi, 4);  // distinct branches, both read intm
    g.print_loop_nest();
    return 0;
}
