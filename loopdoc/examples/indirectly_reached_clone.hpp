#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

[[nodiscard]] int main_impl(bool clone_before, bool swap_compute_at, bool common_clone_in_h_instead=false) {
    try {
        ImageParam in(type_of<float>(), 2, "in");
        Var x("x"), y("y");
        Func common("common");
        common(x, y) = in(x, y) + in(x+1, y+1);
        Func f("f");
        f(x, y) = common(x, y) + common(x+1, y+1);
        Func g("g"), h("h");
        g(x, y) = f(x, y) + f(x+1, y+1);
        h(x, y) = f(x, y+1) + f(x+1, y);
        Func out("out");
        out(x, y) = g(x, y) + g(x+1, y+1) + h(x, y) + h(x+1, y+1);

        Func f_clone_in_g;
        if (clone_before) {
            f_clone_in_g = f.clone_in(g);
        }
        // common_clone_in_g = common.clone_in(g) is the main path of the example.
        Func common_clone_in_g = common_clone_in_h_instead ? common.clone_in(h) : common.clone_in(g);
        if (!clone_before) {
            f_clone_in_g = f.clone_in(g);
        }

        // Situation: out consumes (<-) common along two paths:
        //
        // out <- g <- f_clone_in_g <- (one of the common funcs, clone or original?)
        // out <- h <- f <- (one of the common funcs, clone or original?)
        //
        // When I call common.clone_in(g),
        // intuitive transitivity implies g should consume common_clone_in_g
        // and h should consume the original common.
        //
        // However, when Halide implements the "transitivity" behavior,
        // it ignores that g will not consume the original f (f_clone_in_g instead).
        // Therefore the search goes g -> f -> common and pins the clone on f -- f's
        // read of common is redirected to common_clone_in_g. But post-wrap the
        // original f is consumed only by h (g reads f_clone_in_g), so it is h that
        // ends up consuming common_clone_in_g, while g (via f_clone_in_g, a copy of
        // f's body) keeps reading the original common.
        //
        // This is not visible in print_loop_nest, but the below compute_at probe validates this behavior.
        // The seemingly ridiculous common_clone_in_g.compute_at(h, y) compiles (h consumes g's clone)
        // while the "correct" common_clone_in_g.compute_at(g, y) leads to CompileError.
        //
        // This is asymmetric: common.clone_in(h) still yields the same behavior.
        // The point is the pin always lands on the original f (the direct caller of
        // common in the pre-wrap graph), never on f_clone_in_g -- so the clone is
        // consumed by whoever still reads f, i.e. h.
        //
        // whether f.clone_in(g) happens before or after common.clone_in(g) seems irrelevant.
        // This is predictable, because of Halide's lazy wrap behavior,
        // and ignoring wrappers/clones for transitivity.

        if (swap_compute_at) {
            common_clone_in_g.compute_at(h, y);
            common.compute_at(g, y);
        }
        else {
            common_clone_in_g.compute_at(g, y);
            common.compute_at(h, y);
        }
        g.compute_root();
        h.compute_root();

        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());  // Customize printing at your discretion.
        return 1;
    }
    catch (const InternalError &e) {
        fprintf(stderr, "InternalError: %s\n", e.what());  // Customize printing at your discretion.
        return 1;
    }
    return 0;
}
