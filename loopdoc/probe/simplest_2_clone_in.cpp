#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<float>(), 2, "in");
        Func clone_me("clone_me");
        clone_me(x, y) = in(x, y) + in(x+1, y+1);
        Func c1("c1"), c2("c2");
        c1(x, y) = clone_me(x, y) + clone_me(x+1, y+1);
        c2(x, y) = clone_me(x, y+1) + clone_me(x+1, y);
        Func f("f");
        f(x, y) = c1(x, y) + c1(x+1, y+1) + c2(x, y) + c2(x+1, y+1);
        Func clone_me_clone_in_c1 = clone_me.clone_in(c1);
        // Bug-for-bug compatibility: this raises InternalError.
        Func clone_me_clone_in_c2 = clone_me.clone_in(c2);
        f.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());  // Customize printing at your discretion.
        return 1;
    }
    catch (const InternalError &e) {
        fprintf(stderr, "InternalError: %s\n", e.what());
        return 1;
    }
    return 0;
}
