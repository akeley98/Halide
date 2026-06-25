#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp two_compute_at_test (Halide issue #6367). Two outputs
// (output1, output3) are both fused into output2 at k, while each output has its
// own compute_at producer chain:
//   output1_value.compute_at(output2, k); intermediate.compute_at(output1_value, k)
//   output3_value.compute_at(output3, k)
//   output1.compute_with(output2, k); output3.compute_with(output2, k)
// (vectorize / bound_storage / store_in stripped.) The real test compiles a
// Pipeline {output1, output2, output3}; micro cannot realize multiple outputs,
// so out reads all three as the single output.
int main() {
    try {
        Var k("k");
        ImageParam input1(type_of<int16_t>(), 2, "input1");
        Func output1("output1"), output2("output2"), output3("output3");
        Func intermediate("intermediate"), output1_value("output1_value");
        Func output3_value("output3_value"), out("out");

        intermediate(k) = input1(k, 0) + input1(k, 1);
        output1_value(k) = intermediate(k) + intermediate(k);
        output1(k) = output1_value(k);
        output2(k) = output1_value(k) + output1_value(k);
        output3_value(k) = input1(k, 0) + 2;
        output3(k) = output3_value(k);
        out(k) = output1(k) + output2(k) + output3(k);

        output1.compute_root();   // output1/2/3 were Pipeline outputs (implicitly root)
        output2.compute_root();
        output3.compute_root();
        intermediate.compute_at(output1_value, k);
        output1_value.compute_at(output2, k);
        output1.compute_with(output2, k);
        output3_value.compute_at(output3, k);
        output3.compute_with(output2, k);
        // intermediate computed per output1_value-k: its own k loop collapses.
        micro_halide_collapses(intermediate, {k});
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
