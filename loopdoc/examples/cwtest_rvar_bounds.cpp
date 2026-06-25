#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp rvar_bounds_test. sum_1 and sum_2 each reduce a 2-D RDom
// (r.x, r.y). Each update stage is TILED over its RVars, producing RVar tile
// dims (rxOuter, ryOuter, rxInner, ryInner). The two update stages are then
// fused at the outer RVar tile dim: sum_1.update(0).compute_with(sum_2.update(0),
// rxOuter). Producers (input_c, add_1, mul_2) are computed at sum_2's rxOuter.
// (The original read the RVars via get_schedule().dims(); here we use r.x / r.y
// directly. CheckAllocationSize lowering pass stripped.) total_sum is the output.
//
// NOTE: this is an RVar split/tile + compute_with case. micro_halide accepts
// Var-or-RVar in split/tile/reorder but does not yet propagate the RVar-kind of
// the tile-produced loop names through its per-stage state, so the fused loop
// nest may DIFFER from real Halide here -- an expected finding, not a bug to fix.
int main() {
    try {
        Var x("x"), y("y");
        ImageParam input(type_of<int16_t>(), 2, "input");
        Func input_c("input_c"), add_1("add_1"), mul_2("mul_2");
        Func sum_1("sum_1"), sum_2("sum_2"), total_sum("total_sum");
        RDom r(0, 32, 0, 64, "r");

        input_c(x, y) = input(x, y);
        add_1(x, y) = input_c(x, y) + 1;
        mul_2(x, y) = input_c(x, y) * 2;
        sum_1() = cast<int16_t>(0);
        sum_2() = cast<int16_t>(0);
        sum_1() += add_1(r.x, r.y);
        sum_2() += mul_2(r.x, r.y);
        total_sum() = sum_1() + sum_2();

        RVar rxOuter("rxOuter"), rxInner("rxInner");
        RVar ryOuter("ryOuter"), ryInner("ryInner");

        sum_1.update(0).tile(r.x, r.y, rxOuter, ryOuter, rxInner, ryInner, 8, 8);
        sum_2.update(0).tile(r.x, r.y, rxOuter, ryOuter, rxInner, ryInner, 8, 8);

        add_1.compute_at(sum_2, rxOuter);
        mul_2.compute_at(sum_2, rxOuter);
        input_c.compute_at(sum_2, rxOuter);

        sum_1.update(0).compute_with(sum_2.update(0), rxOuter);
        sum_1.compute_root();
        sum_2.compute_root();
        total_sum.compute_root();
        total_sum.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
