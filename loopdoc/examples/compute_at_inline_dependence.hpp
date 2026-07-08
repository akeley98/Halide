#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

enum class IntmCase
{
    pure_inline,
    update_inline,
    compute_root,
};

[[nodiscard]] int main_impl(IntmCase intm_case)
{
    try {
        Var x("x"), y("y"), z("z");
        ImageParam in(type_of<float>(), 3, "in");
        Func p("p");
        p(x, y, z) = in(x+1, y+1, z+1) + in(x, y, z);
        Func intm("intm");
        intm(x, y, z) = p(x, y, z) + p(x+1, y+1, z+1);
        if (intm_case == IntmCase::update_inline) {
            intm(x, y, z) += 4;
        }
        Func out("out");
        out(x, y, z) = intm(x, y, z) + intm(x+1, y+1, z+1);

        p.compute_at(out, y);

        if (intm_case == IntmCase::compute_root) {
            intm.compute_root();
        }

        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
#ifndef USE_MICRO_HALIDE
    catch (const InternalError &e) {
        fprintf(stderr, "InternalError: %s\n", e.what());
        return 1;
    }
#endif
    return 0;
}
