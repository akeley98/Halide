#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
#include <stdio.h>

// PROBE (real Halide only): what happens with 4+ nested gpu_block loops?
// GPU blocks map onto blockIdx.x/y/z -- only 3 dimensions. Same question as the
// threads probe.
static void banner(const char *s){ fprintf(stderr, "\n===== %s =====\n", s); }

int main() {
    Var x("x"), b0("b0"), b1("b1"), b2("b2");
    banner("4 nested gpu_blocks");
    try {
        ImageParam in(type_of<int>(), 1, "in");
        Func f("f"); f(x) = in(x);
        f.split(x, x, b0, 4);
        f.split(x, x, b1, 4);
        f.split(x, x, b2, 4);
        f.gpu_blocks(b0);
        f.gpu_blocks(b1);
        f.gpu_blocks(b2);
        f.gpu_blocks(x);        // the 4th block loop
        f.print_loop_nest();
    } catch (const Halide::Error &e) {
        fprintf(stderr, "EXCEPTION: %s\n", e.what());
    }
    return 0;
}
