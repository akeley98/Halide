#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#include <stdio.h>
// Group {a,z} fused. mm non-member, a < mm < z. c1 reads (one member)+mm; c2
// reads the OTHER member. Only difference between the two runs: which member c1
// reads. If the group had a single key, group-vs-mm order would be fixed.
static void run(const char* label, bool c1_reads_z){
    Var x("x"); ImageParam in(type_of<int>(),1,"in");
    Func a("a"); a(x)=in(x);
    Func z("z"); z(x)=in(x)+1;
    Func mm("mm"); mm(x)=in(x)+2;
    Func c1("c1"), c2("c2");
    if(!c1_reads_z){ c1(x)=a(x)+mm(x); c2(x)=z(x); }   // c1 reads member a
    else          { c1(x)=z(x)+mm(x); c2(x)=a(x); }   // c1 reads member z
    Func out("out"); out(x)=c1(x)+c2(x);
    a.compute_root(); z.compute_root(); mm.compute_root(); c1.compute_root(); c2.compute_root();
    a.compute_with(z, x);
    printf("=== %s ===\n", label);
    out.print_loop_nest();
}
int main(){ run("c1 reads member a", false); run("c1 reads member z", true); return 0; }

// FINDING (2026-07-09): the realization-order tie-break key of a fused group,
// as seen from a consumer, is the key of the SPECIFIC MEMBER that consumer reads
// -- it cannot be deduced from the group node alone. Observable: with group
// {a,z} (a.compute_with(z,x)) and non-member mm where a < mm < z, flipping which
// member c1 reads flips the group's position relative to mm:
//   c1 reads a : produce z{produce a} ... produce mm   (group BEFORE mm)
//   c1 reads z : produce mm ... produce z{produce a}    (mm BEFORE group)
// Source (RealizationOrder.cpp): the only collapsed edge is member -> _fgGroup;
// a consumer's out-edges are stored as the ORIGINAL member names, and
// sort_funcs_by_name_and_counter keys on the member name/visitation. So the
// "which member" identity rides on the edge; the group node aggregates only the
// members' OUTGOING edges (union of producers). Confirms the human's
// "edge-annotation" model over a "group node with a single key" model.
// (micro_halide's collapse_to_items last-member heuristic models neither faithfully.)
