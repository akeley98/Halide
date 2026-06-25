#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp store_at_different_levels_test. Two producers fused at y
// but with DIFFERENT store levels: producer1 is compute_at(consumer, y) (store
// level defaults to compute level), while producer2 is store_root() +
// compute_at(consumer, y) and fused into producer1 at y. Only the COMPUTE level
// must match for compute_with; the store levels may differ -- producer2's
// `store` node sits at the root, wrapping the whole consumer body.
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func producer1("producer1"), producer2("producer2"), consumer("consumer");
        producer1(x, y) = in(x, y);
        producer2(x, y) = in(x, y) + 1;
        consumer(x, y) = producer1(x, y) + producer2(x, y);
        consumer.compute_root();
        producer1.compute_at(consumer, y);
        producer2.store_root().compute_at(consumer, y).compute_with(producer1, y);
        // Both producers are computed per consumer-y, so their own y collapses.
        micro_halide_collapses(producer1, {y});
        micro_halide_collapses(producer2, {y});
        consumer.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
