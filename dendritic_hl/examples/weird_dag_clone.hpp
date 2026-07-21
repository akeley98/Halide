#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

enum class CloneAt
{
    out,
    c2,
    c1,
};

[[nodiscard]] int main_impl(CloneAt clone_at, bool enable_c3)
{
    try {
        Var x("x"), y("y");
        ImageParam in1(type_of<float>(), 2, "in");
        ImageParam in2(type_of<float>(), 2, "in");
        Func maybe_inline("maybe_inline");
        maybe_inline(x, y) = in1(x, y) * 4;
        Func clone_me("clone_me");
        clone_me(x, y) = maybe_inline(x, y) * 2 + maybe_inline(x+1, y+1);
        Func c1("c1");
        c1(x, y) = clone_me(x, y+1) + clone_me(x+1, y);
        // c2 consumes clone_me both directly and via c1.
        Func c2("c2");
        c2(x, y) = clone_me(x, y+1) + clone_me(x+1, y) + c1(x, y) + c1(x+1, y+1);

        Func out("out");

        if (enable_c3)
        {
            Func c3("c3");
            c3.compute_root();
            c3(x, y) = clone_me(x+1, y+1) + clone_me(x-1, y-1);
            out(x, y) = c2(x, y+1) + c2(x+1, y) + c3(x, y) + c3(x+1, y+1);
        }
        else
        {
            out(x, y) = c2(x, y+1) + c2(x+1, y);
        }

        // cloned is distinguished from clone_me by not inlining maybe_inline.
        Func cloned = clone_me.clone_in(
            clone_at == CloneAt::out ? out :
            clone_at == CloneAt::c2 ? c2 : c1
        );
        maybe_inline.in(cloned).compute_at(cloned, y);

        clone_me.compute_root();
        cloned.compute_root();
        c2.compute_root();
        c1.compute_at(c2, y);

        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    catch (const InternalError &e) {
        fprintf(stderr, "InternalError: %s\n", e.what());
        return 1;
    }
    return 0;
}
