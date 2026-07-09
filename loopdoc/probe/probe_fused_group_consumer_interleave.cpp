// Probe: micro_halide's fused-group realization-order placement diverges from
// Halide when a CONSUMER of one group member is interleaved (in the per-Func
// realization order) before another member.
//
//   fused group {a, z}   (a.compute_with(z, x));  both compute_root
//   c reads member a;    out reads z and c        (c sorts before z)
//
// Halide (RealizationOrder.cpp): the group is a dummy node whose inputs are the
// UNION of members' external producers (here just `in`), reached via c -> a ->
// dummy, so the whole group is placed BEFORE c. Output: produce z {produce a}
// ... consume -> produce c -> out.
//
// micro_halide: computes a plain per-Func post-order realization order, then
// collapse_to_items places each group at the position of its LAST member in that
// flat order (here z, which sorts after c). So micro emits `produce c` FIRST,
// then the group -- but c consumes member a, so this is a CONSUMER-BEFORE-
// PRODUCER order. BUG.
//
// FINDING (2026-07-09), real vs micro top-level order:
//   REAL : produce z { produce a }  ... consume ... produce c ... produce out
//   MICRO: produce c ...             consume c: produce z { produce a } ... out
// Root cause: micro's "group at last-member slot" heuristic is faithful to
// Halide for a PRODUCER interleaved between members (cwtest_update_stage_rfactor),
// but not for a CONSUMER of an earlier member interleaved before a later member.
// The correct rule (loopdoc §16 step 2) is Halide's: collapse the group to one
// node whose inputs are the union of members' external producers. Untested by the
// current suite (all green); this is a micro implementation gap, not a doc gap.
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
#include <stdio.h>
int main(){
    Var x("x"); ImageParam in(type_of<int>(),1,"in");
    Func a("a"); a(x)=in(x);
    Func z("z"); z(x)=in(x)+1;
    Func c("c"); c(x)=a(x)*2;
    Func out("out"); out(x)=z(x)+c(x);
    a.compute_root(); z.compute_root(); c.compute_root();
    a.compute_with(z, x);
    out.print_loop_nest();
    return 0;
}
