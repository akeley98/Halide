#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

[[nodiscard]] int clone_in_but_inlined(
    bool inlined,
    bool clone_in_both,
    bool common_compute_root,
    bool no_c3=false)
{
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<float>(), 2, "in");
        Func weird("weird");
        weird(x, y) = in(x, y) * 1337;
        Func common("common");
        common(x, y) = in(x, y + 1) + in(x + 1, y) + weird(x, y) + weird(x + 1, y + 1);
        Func maybe_inlined("maybe_inlined");
        maybe_inlined(x, y) = common(x, y + 1) + common(x + 1, y);
        Func c1("c1");
        c1(x, y) = maybe_inlined(x, y + 1) + maybe_inlined(x + 1, y);
        Func c2("c2");
        c2(x, y) = common(x, y) + in(x + 1, y + 1) + common(x + 1, y + 1);
        Func out("out");
        if (no_c3)
        {
            out(x, y) = c1(x, y) + c1(x+1, y+1) + c2(x, y) + c2(x+1, y+1);
        }
        else
        {
            // c3 is weird because the transitivity of clone_in means maybe_inlined will use the
            // cloned common, but c3 also uses the original common directly.
            Func c3("c3");
            c3(x, y) = maybe_inlined(x, y) + maybe_inlined(x + 1, y + 1) + common(x, y) + common(x + 1, y + 1);
            out(x, y) = c1(x, y) + c1(x+1, y+1) + c2(x, y) + c2(x+1, y+1) + c3(x, y) + c3(x + 1, y + 1);
            c3.compute_at(out, x);
            // If we clone again, should maybe_inlined use common_clone_in_c1 or common_clone_in_c3?
            // Turn out the following two lines crash Halide.
            // I'm just going to ignore this for now.
            // Func hmm = common.clone_in(c3);
            // hmm.compute_root();
        }

        Func common_clone = clone_in_both ? common.clone_in({c1, c2}) : common.clone_in(c1);
        common_clone.compute_root();

        weird.compute_root();
        c1.compute_at(out, y);
        c2.compute_at(out, y);

        if (common_compute_root) {
            common.compute_root();
        }
        else {
            common.compute_at(out, y);
        }

        if (!inlined) {
            maybe_inlined.compute_root();
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
