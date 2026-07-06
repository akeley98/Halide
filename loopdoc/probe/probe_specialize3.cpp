#include "Halide.h"
#include <cstdio>
using namespace Halide;

static void banner(const char *s) { fprintf(stderr, "\n==================== %s ====================\n", s); }

int main() {
    ImageParam in(type_of<uint8_t>(), 2, "in");

    // --- Producer itself specialized ---------------------------------------
    {
        banner("producer g specialized, consumed by f");
        Var x("x"), y("y"), xi("xi"), yi("yi");
        Func g("g"), f("f");
        g(x, y) = in(x, y);
        f(x, y) = g(x, y);
        Param<bool> cond;
        g.compute_root();
        g.specialize(cond).tile(x, y, xi, yi, 4, 4);
        f.print_loop_nest();
    }

    // --- compute_with + specialize (expect: illegal) -----------------------
    // Run this LAST because it aborts. We fork via a separate process check.
    return 0;
}
