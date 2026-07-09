#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
#include <stdio.h>

// PROBE (real Halide only): what happens with 4+ nested gpu_thread loops?
// GPU threads map onto threadIdx.x/y/z -- only 3 dimensions exist. Does
// print_loop_nest (which skips the GPU lowering passes FuseGPUThreadLoops etc.)
// error, or just print 4 gpu_thread loops? Build 4 nested dims by repeated
// split, then gpu_threads each.
static void banner(const char *s){ fprintf(stderr, "\n===== %s =====\n", s); }

int main() {
    Var x("x"), t0("t0"), t1("t1"), t2("t2");
    banner("4 nested gpu_threads");
    try {
        ImageParam in(type_of<int>(), 1, "in");
        Func f("f"); f(x) = in(x);
        f.split(x, x, t0, 4);   // [t0, x]
        f.split(x, x, t1, 4);   // [t0, t1, x]
        f.split(x, x, t2, 4);   // [t0, t1, t2, x]
        f.gpu_threads(t0);
        f.gpu_threads(t1);
        f.gpu_threads(t2);
        f.gpu_threads(x);       // the 4th thread loop
        f.print_loop_nest();
    } catch (const Halide::Error &e) {
        fprintf(stderr, "EXCEPTION: %s\n", e.what());
    }
    return 0;
}
