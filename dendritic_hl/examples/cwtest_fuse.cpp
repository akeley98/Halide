#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp fuse_test. Both f and g fuse (x, y) into a single t
// dimension, then g.compute_with(f, t) shares that fused loop. Below t each
// member keeps its own z loop. (The original parallel(t) and trace are stripped;
// only the loop structure is kept.)
//
//   produce f:
//     produce g:
//       for fused.t:          # the fused (x,y)->t loop, shared
//         for z: f
//         for z: g
//   consume f: consume g: produce h: ...
int main() {
    try {
        Var x("x"), y("y"), z("z"), t("t");
        ImageParam in(type_of<uint8_t>(), 3, "in");
        Func f("f"), g("g"), h("h");
        f(x, y, z) = in(x, y, z);
        g(x, y, z) = in(x, y, z) + 1;
        h(x, y, z) = f(x, y, z) + g(x, y, z);
        f.compute_root();
        g.compute_root();
        f.fuse(x, y, t);
        g.fuse(x, y, t);
        g.compute_with(f, t);
        h.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
