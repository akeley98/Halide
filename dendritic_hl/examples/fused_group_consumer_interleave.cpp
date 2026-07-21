#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A fused group is placed in realization order as ONE node whose inputs are the
// union of the members' external producers (loopdoc.md §16 step 2 / §14) -- so
// the whole group precedes every consumer of any member.
//
// Here the group is {a, z} (a.compute_with(z, x)); `c` reads member `a`; `out`
// reads `z` and `c`. Because `c` sorts before `z`, a naive per-Func realization
// order interleaves `c` between the members `a` and `z`. Halide still realizes
// the whole group BEFORE `c` (c consumes member a, so c is a consumer of the
// group). Expected top-level order: the group (produce z { produce a }), then c,
// then out.
//
// This distinguishes the correct "group as one node, after the union of its
// members' inputs" rule from the tempting "place the group at its last member's
// position in the per-Func order" shortcut, which would wrongly emit `c` first.
int main() {
    Var x("x");
    ImageParam in(type_of<int>(), 1, "in");
    Func a("a"); a(x) = in(x);
    Func z("z"); z(x) = in(x) + 1;
    Func c("c"); c(x) = a(x) * 2;              // consumer of member a
    Func out("out"); out(x) = z(x) + c(x);
    a.compute_root(); z.compute_root(); c.compute_root();
    a.compute_with(z, x);
    out.print_loop_nest();
    return 0;
}
