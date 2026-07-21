#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp update_stage_diagonal_test. Three Funcs f, g, h (each
// pure + 2 updates) joined by "diagonal" cross-stage fuse edges:
//   f.update(1).compute_with(g.update(0), y);
//   g.update(0).compute_with(h, y);
// f.update(0).unscheduled() / g.update(1).unscheduled() tell Halide those stages
// need not be explicitly scheduled (load-bearing: keep real Halide from emitting
// a Warning). out = f + g + h is the single output to print (real test realizes
// Pipeline{f,g,h}).
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func f("f"), g("g"), h("h"), out("out");
        g(x, y) = in(x, y);
        g(x, y) = in(x, y) + g(x, y);
        g(x, y) = in(x, y) + g(x, y);
        f(x, y) = in(x, y);
        f(x, y) = in(x, y) + f(x, y);
        f(x, y) = in(x, y) + f(x, y);
        h(x, y) = in(x, y);
        h(x, y) = in(x, y) + h(x, y);
        h(x, y) = in(x, y) + h(x, y);
        out(x, y) = f(x, y) + g(x, y) + h(x, y);
        f.compute_root();
        g.compute_root();
        h.compute_root();
        f.update(1).compute_with(g.update(0), y);
        g.update(0).compute_with(h, y);
        f.update(0).unscheduled();
        g.update(1).unscheduled();
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
