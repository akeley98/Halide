#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

[[nodiscard]] int main_impl(bool compute_at_child) {
    try {
        Var x("x"), y("y"), z("z");
        ImageParam in(type_of<uint8_t>(), 3, "in");
        ImageParam in2(type_of<uint8_t>(), 3, "in");
        Func parent("parent"), child("child"), h("h"), g("g"), out("out");

        parent(x, y, z) = in(x, y, z);
        g(x, y, z) = in2(x, y, z);
        h(x, y, z) = g(x, y, z);
        child(x, y, z) = in(x, y, z) + h(x, y, z) + h(x + 1, y, z) + h(x, y + 1, z) + h(x, y, z + 1);
        out(x, y, z) = parent(x, y, z) + child(x, y, z);

        parent.compute_root();
        child.compute_root();
        out.compute_root();

        child.compute_with(parent, y);
        if (compute_at_child) {
            g.compute_at(child, z);  // Actually realizes g per y-iteration, not z-iteration.
        }
        else {
            g.compute_at(parent, z);
        }

        g.store_root();

        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
