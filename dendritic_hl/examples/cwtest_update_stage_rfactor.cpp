#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp update_stage_rfactor_test. cost reduces over r.x with
// two update stages, one reading f0 and one reading f1. Each reduction is moved
// into its own Func via rfactor({}) (factor the WHOLE reduction into a fresh
// 1-D-reduction Func). The two intermediates' update stages are then fused at
// the reduction var r.x: tmp1.update().compute_with(tmp2.update(), r.x).
// cost is the single output (it is what the real test realizes).
int main() {
    try {
        Var x("x");
        ImageParam in(type_of<uint8_t>(), 1, "in");
        Func f0("f0"), f1("f1"), cost("cost");
        f0(x) = in(x);
        f1(x) = in(x);
        RDom r(0, 100, "r");
        cost() = 0;
        cost() += f0(r.x);
        cost() += f1(r.x);
        f0.compute_root();
        f1.compute_root();
        // Move each reduction into its own Func (factor the whole reduction:
        // empty preserved list).
        Func tmp1 = cost.update(0).rfactor({});
        Func tmp2 = cost.update(1).rfactor({});
        tmp1.compute_root();
        tmp2.compute_root();
        // Now fuse the two intermediates' reduction loops.
        tmp1.update().compute_with(tmp2.update(), r.x);
        cost.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
