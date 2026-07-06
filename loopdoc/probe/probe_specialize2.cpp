#include "Halide.h"
#include <cstdio>
using namespace Halide;

static void banner(const char *s) { printf("\n==================== %s ====================\n", s); }

int main() {
    ImageParam in(type_of<uint8_t>(), 2, "in");

    // --- Case A: producer computed at DIFFERENT loops per branch ----------
    // The Func.h trick: rename so g.compute_at(f, g_loop) means compute_at(f,y)
    // in one branch and compute_at(f,x) in the other.
    {
        banner("caseA producer compute_at different loop per specialization");
        Var x("x"), y("y");
        Func g("g"), f("f");
        g(x, y) = in(x, y);
        f(x, y) = g(x, y);
        Param<bool> cond;
        f.compute_root().specialize(cond);
        Var g_loop;
        f.specialize(cond).rename(y, g_loop);
        f.rename(x, g_loop);
        g.compute_at(f, g_loop);
        Func out("out");
        out(x, y) = f(x, y);
        out.print_loop_nest();
    }

    // --- Case B: else-if chain (two specializations) that DIFFER ----------
    {
        banner("caseB two specializations differing in structure");
        Var x("x"), y("y"), xi("xi"), yi("yi");
        Func f("f");
        f(x, y) = in(x, y);
        Param<bool> c1, c2;
        f.compute_root();
        f.specialize(c1).tile(x, y, xi, yi, 4, 4);       // 4 loops
        f.specialize(c2).split(x, x, xi, 8);              // 3 loops
        // default: 2 loops
        Func out("out");
        out(x, y) = f(x, y);
        out.print_loop_nest();
    }

    // --- Case C: consumer specialized, producer differs, plus a non-pure --
    {
        banner("caseC specialization only changes producer compute site");
        Var x("x"), y("y");
        Func g("g"), f("f");
        g(x, y) = in(x, y);
        f(x, y) = g(x, y);
        Param<bool> cond;
        f.compute_root();
        // default: g compute_at f.x ; specialized: g compute_root (hoisted out)
        g.compute_at(f, x);
        f.specialize(cond);  // no schedule change on f itself in this branch
        Func out("out");
        out(x, y) = f(x, y);
        out.print_loop_nest();
    }

    return 0;
}
