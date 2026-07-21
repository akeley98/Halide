#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Task 3: a non-trivial specialize TREE mixing rfactor before/after branch forks,
// plus a tile (loopdoc.md §1/§6/§12/§15). Build order:
//   b1 = g.update(0).specialize(c1);      // B1 forks while g is still naive
//   Func intm1 = b1.rfactor(r.y, u1);     // rfactor B1 -> its OWN intm1
//   b1.specialize(c1b).split(x,...);      // nested child B1b of B1 (aliases intm1)
//   Func intm_base = g.update(0).rfactor(r.y, ub);  // rfactor the BASE (after B1 forked)
//   intm_base.compute_root().tile(...);   // tile the shared intermediate
//   g.update(0).specialize(c2).split(x,...); // B2 forks AFTER the base rfactor -> aliases intm_base
// So the tree is:
//   if c1: { if c1b: B1b(split, reads intm1)  else: B1(reads intm1) }
//   else if c2: B2(split x, reads intm_base)
//   else:       fallback(reads intm_base)
// intm1 is shared by B1/B1b; intm_base (tiled) is shared by B2/fallback. This
// exercises rfactor-BEFORE a fork (B2 aliases the post-rfactor base) and
// rfactor-ON a branch (B1's own intm1) in one specialize tree.
//
// B2 is given its OWN split so it is structurally distinct from the fallback.
// Without it, B2 and fallback are identical (both naive, both reading intm_base)
// and real Halide MERGES them via the §15 identical-branch simplify -- a feature
// micro_halide does not implement (out of scope). Distinguishing B2 keeps this
// example focused on the §6 realization/visitation order it is meant to test.
//
// Verified against real Halide:
//   produce g_intm:                                   # intm_base, tiled (4-loop init)
//     for ub.txu: for x.tx: for ub.tui: for x.txi: g_intm(...)
//     for x: for ub: for r: g_intm(...)               # partial
//   consume g_intm:
//     produce g_intm:                                 # intm1, plain (2-loop init)
//       for u1: for x: g_intm(...)
//       for x: for u1: for r: g_intm(...)             # partial
//     consume g_intm:
//       produce g:
//         for x: g(...)                               # g init
//         for x.bxo: for x.bxi: for r: g(...)         # B1b (split x)
//         for x: for r: g(...)                        # B1
//         for x.cxo: for x.cxi: for r: g(...)         # B2 (split x)
//         for x: for r: g(...)                        # fallback
//
// The observable that this example targets is the two intermediates' realization
// ORDER (both print as `g_intm`): micro must visit the base-definition producer
// before the specialization-branch producer (§6 base-before-specialization).
int main() {
    Var x("x"), u1("u1"), ub("ub"), bxo("bxo"), bxi("bxi"),
        tx("tx"), tu("txu"), txi("txi"), tui("tui"), cxo("cxo"), cxi("cxi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func g("g");
    RDom r(0, 10, 0, 10, "r");
    g(x) = 0;
    g(x) += cast<int32_t>(in(r.x, r.y));
    Param<bool> c1, c1b, c2;
    Stage b1 = g.update(0).specialize(c1);
    Func intm1 = b1.rfactor(r.y, u1);
    b1.specialize(c1b).split(x, bxo, bxi, 4);
    Func intm_base = g.update(0).rfactor(r.y, ub);
    intm_base.compute_root().tile(x, ub, tx, tu, txi, tui, 4, 4);
    intm1.compute_root();
    g.update(0).specialize(c2).split(x, cxo, cxi, 4);
    g.print_loop_nest();
}
