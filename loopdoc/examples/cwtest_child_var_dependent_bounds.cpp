#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// From compute_with.cpp child_var_dependent_bounds_test (Halide issue #8149).
// f and g each have a pure stage plus an update that reduces over RDom r and
// reads an intermediate (f_inter / g_inter). Both intermediates are computed at
// f's update RVar r (f_inter.compute_at(f, r), g_inter.compute_at(f, r)), and
// g's update fuses into f's update at r:
//   g.update().compute_with(f.update(), r);
// f.update().unscheduled() is load-bearing here: it marks f's update as not
// needing an explicit schedule (and keeps real Halide from emitting a Warning).
// The real test realizes Pipeline{f, g}; micro cannot, so out = f + g is the
// single output to print. (realize/buffers/set_min/checks stripped.)
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<int>(), 1, "in");
        Func f("f"), g("g"), out("out");
        Func f_inter("f_inter"), g_inter("g_inter");
        RDom r(0, 10, "r");

        f_inter(x, y) = x;
        f_inter(x, y) += 1;
        f(x) = x;
        f(x) += f_inter(x, r);

        g_inter(x, y) = x;
        g_inter(x, y) += 1;
        g(x) = x;
        g(x) += g_inter(x, r);

        out(x) = f(x) + g(x);

        f.compute_root();
        g.compute_root();
        f_inter.compute_at(f, r);
        g_inter.compute_at(f, r);
        g.update().compute_with(f.update(), r);
        f.update().unscheduled();

        // Declared point-loop elision (ground truth from real Halide; bounds
        // inference is out of scope, see README / loopdoc §14). f_inter is read
        // as f_inter(x, r) at f's update loop, so at each (x, r) it is a single
        // point in both dims -> both x and y collapse, in both its stages.
        // g_inter feeds the fused CHILD g, whose x is the shared fused.x; its
        // required x-region spans (the "child var dependent bounds"), so only y
        // collapses.
        micro_halide_collapses(f_inter, {x, y});
        micro_halide_collapses(f_inter.update(), {x, y});
        micro_halide_collapses(g_inter, {y});
        micro_halide_collapses(g_inter.update(), {y});

        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
