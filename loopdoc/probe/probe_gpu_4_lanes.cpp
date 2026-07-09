#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
#include <stdio.h>

// PROBE (real Halide only): what happens with 4+ nested gpu_lane loops? There is
// only one lane (warp) dimension, so >1 gpu_lanes is already suspect; test 4.
// Also test a mixed deep nest: 1 block + 4 threads to see if the thread limit is
// what triggers (vs. total GPU-loop count).
static void banner(const char *s){ fprintf(stderr, "\n===== %s =====\n", s); }

int main() {
    { Var x("x"), l0("l0"), l1("l1"), l2("l2");
      banner("4 nested gpu_lanes");
      try {
        ImageParam in(type_of<int>(), 1, "in");
        Func f("f"); f(x) = in(x);
        f.split(x, x, l0, 4);
        f.split(x, x, l1, 4);
        f.split(x, x, l2, 4);
        f.gpu_lanes(l0);
        f.gpu_lanes(l1);
        f.gpu_lanes(l2);
        f.gpu_lanes(x);
        f.print_loop_nest();
      } catch (const Halide::Error &e) { fprintf(stderr, "EXCEPTION: %s\n", e.what()); }
    }

    { Var x("x"), b("b"), t0("t0"), t1("t1"), t2("t2");
      banner("1 block + 4 threads");
      try {
        ImageParam in(type_of<int>(), 1, "in");
        Func f("f"); f(x) = in(x);
        f.split(x, x, t0, 4);
        f.split(x, x, t1, 4);
        f.split(x, x, t2, 4);
        f.split(x, x, b, 4);    // [t0, t1, t2, b, x] -> outermost is x
        f.gpu_threads(t0);
        f.gpu_threads(t1);
        f.gpu_threads(t2);
        f.gpu_threads(b);       // 4th thread
        f.gpu_blocks(x);        // 1 block outermost
        f.print_loop_nest();
      } catch (const Halide::Error &e) { fprintf(stderr, "EXCEPTION: %s\n", e.what()); }
    }
    return 0;
}
