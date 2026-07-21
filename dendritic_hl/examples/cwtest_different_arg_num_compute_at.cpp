#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp different_arg_num_compute_at_test. The two fused Funcs
// have DIFFERENT dimensionality: output_a is 3-D (x, y, c) and output_b is 1-D
// (c). They fuse at the shared dim c: output_b.compute_with(output_a, c). big
// and reduce_big are computed at output_a's c loop. (count_leading_zeros stripped
// to a plain read; real test realizes Pipeline{output_a, output_b}, so out reads
// both as the single output.)
int main() {
    try {
        Var x("x"), y("y"), c("c");
        ImageParam in(type_of<uint8_t>(), 3, "in");
        Func big("big"), output_a("output_a"), reduce_big("reduce_big"),
            output_b("output_b"), out("out");
        big(x, y, c) = in(x, y, c);
        reduce_big(c) = c;
        output_a(x, y, c) = big(x, y, c) + reduce_big(c);
        output_b(c) = reduce_big(c) * 5;
        out(x, y, c) = output_a(x, y, c) + output_b(c);
        output_a.compute_root();   // output_a, output_b were Pipeline outputs (implicitly root)
        output_b.compute_root();
        output_b.compute_with(output_a, c);
        big.compute_at(output_a, c);
        reduce_big.compute_at(output_a, c);
        // big is computed per output_a-c, so its own c loop collapses to a point.
        micro_halide_collapses(big, {c});
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
