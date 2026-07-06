#include "Halide.h"
#include <cstdio>
using namespace Halide;

// Probe: how does print_loop_nest() render specializations?
// PrintLoopNest.cpp has NO visit(IfThenElse), so we expect the default
// IRVisitor to walk both branches and print them concatenated with no
// if/else marker. Confirm empirically, and test schedule inheritance,
// nested specialize, specialize_fail, and a compute_at producer in branches.

static void banner(const char *s) { printf("\n==================== %s ====================\n", s); }

int main() {
    ImageParam in(type_of<uint8_t>(), 2, "in");

    // --- Case 1: basic specialize, different schedule per branch -----------
    {
        banner("case1 basic specialize (unroll in one branch)");
        Var x("x"), y("y");
        Func f("f");
        f(x, y) = in(x, y);
        Param<bool> cond;
        f.compute_root();
        f.specialize(cond).tile(x, y, x, y, Var("xi"), Var("yi"), 4, 4);
        Func out("out");
        out(x, y) = f(x, y);
        out.print_loop_nest();
    }

    // --- Case 2: nested specialize -----------------------------------------
    {
        banner("case2 nested specialize");
        Var x("x"), y("y");
        Func f("f");
        f(x, y) = in(x, y);
        Param<bool> c1, c2;
        f.compute_root();
        f.specialize(c1).specialize(c2);
        Func out("out");
        out(x, y) = f(x, y);
        out.print_loop_nest();
    }

    // --- Case 3: specialize_fail -------------------------------------------
    {
        banner("case3 specialize_fail");
        Var x("x"), y("y");
        Func f("f");
        f(x, y) = in(x, y);
        Param<bool> c1;
        f.compute_root();
        f.specialize(c1).tile(x, y, x, y, Var("xi"), Var("yi"), 4, 4);
        f.specialize_fail("no default");
        Func out("out");
        out(x, y) = f(x, y);
        out.print_loop_nest();
    }

    // --- Case 4: producer compute_at inside specialized consumer -----------
    {
        banner("case4 producer compute_at, consumer specialized differently");
        Var x("x"), y("y");
        Func g("g"), f("f");
        g(x, y) = in(x, y);
        f(x, y) = g(x, y);
        Param<bool> cond;
        f.compute_root();
        // In the specialized branch, compute g at x; default computes g at root.
        g.compute_at(f, x);
        f.specialize(cond);
        Func out("out");
        out(x, y) = f(x, y);
        out.print_loop_nest();
    }

    return 0;
}
