#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
#include <stdio.h>

// PROBE (real Halide only): the companion to probe_gpu_4_{threads,blocks,lanes}.
// Those show that print_loop_nest happily prints 4+ nested GPU loops WITHOUT
// error, because it skips the GPU lowering passes (FuseGPUThreadLoops /
// CanonicalizeGPUVars). This probe drives FULL lowering (compile_to_module with a
// CUDA target) to show where the limit actually fires:
//   - all-threads (no block): "GPU thread loop ... must be inside a GPU block
//     loop" fires first (the block-enclosure rule).
//   - 1 block + 4 threads:    "The maximum number of nested GPU thread loops is 3."
// Either way the >3 GPU-dim limit is a lowering-time check, invisible to
// print_loop_nest -- which is why loopdoc §17 declares GPU legality out of scope
// and micro_halide need not enforce any GPU-loop-count limit.
static void banner(const char *s){ fprintf(stderr, "\n===== %s =====\n", s); }

int main() {
    Target tgt = get_host_target();
    tgt.set_feature(Target::CUDA);

    { Var x("x"), t0("t0"), t1("t1"), t2("t2");
      banner("full lowering: 4 threads, no block");
      try {
        ImageParam in(type_of<int>(), 1, "in");
        Func f("f"); f(x) = in(x);
        f.split(x, x, t0, 4); f.split(x, x, t1, 4); f.split(x, x, t2, 4);
        f.gpu_threads(t0); f.gpu_threads(t1); f.gpu_threads(t2); f.gpu_threads(x);
        f.compile_to_module({in}, "f", tgt);
        fprintf(stderr, "LOWERED OK\n");
      } catch (const Halide::Error &e) { fprintf(stderr, "LOWER EXCEPTION: %s\n", e.what()); }
    }

    { Var x("x"), b("b"), t0("t0"), t1("t1"), t2("t2");
      banner("full lowering: 1 block + 4 threads");
      try {
        ImageParam in(type_of<int>(), 1, "in");
        Func f("f"); f(x) = in(x);
        f.split(x, x, t0, 4); f.split(x, x, t1, 4); f.split(x, x, t2, 4); f.split(x, x, b, 4);
        f.gpu_threads(t0); f.gpu_threads(t1); f.gpu_threads(t2); f.gpu_threads(b);
        f.gpu_blocks(x);
        f.compile_to_module({in}, "f", tgt);
        fprintf(stderr, "LOWERED OK\n");
      } catch (const Halide::Error &e) { fprintf(stderr, "LOWER EXCEPTION: %s\n", e.what()); }
    }
    return 0;
}
