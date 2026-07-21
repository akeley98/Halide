#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE: all members of a fused group must share the SAME compute level.
// Here f is compute_root but g is compute_at(out, y), so fusing them is
// illegal -- Halide errors "Invalid compute_with: the compute levels of g.s0
// (out.y) and f.s0 (.__root) do not match". (The whole group is injected as one
// loop nest at a single compute level; two different levels have no single
// injection point. Only the COMPUTE level must match -- store levels may differ.)
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), out("out");
        f(x, y) = in(x, y);
        g(x, y) = in(x, y);
        out(x, y) = f(x, y) + g(x, y);
        f.compute_root();
        g.compute_at(out, y);     // different compute level from f
        g.compute_with(f, x);     // illegal: compute levels do not match
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
