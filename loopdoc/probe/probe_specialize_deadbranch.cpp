#include "Halide.h"
#include <cstdio>
using namespace Halide;

// ============================================================================
// WHEN does the specialize "dead branch" get eliminated, relative to schedule
// legality checks?  (Does scheduling a producer that is DEAD in some branch
// trigger errors from that dead branch?)
//
// Setup in every case: f(x,y) = select(cond, g, gc); f.specialize(cond). The
// simplifier propagates the condition into each branch's definition BEFORE the
// scheduler injects producers -- print_loop_nest order (PrintLoopNest.cpp):
//   realization_order -> simplify_specializations(env) -> schedule_functions.
// So in the cond-true branch f's body is just `g` and in the fallback it is
// just `gc`. A producer PRUNED from a branch is never injected there.
//
// CONSEQUENCE (verified below): the dead side imposes NO scheduling constraints.
// A producer's compute_at level only has to be valid in the branch(es) where it
// is actually USED. So the worry "an illegal schedule on the dead side of the
// branch fails the whole thing" does NOT happen. The only constraint is the
// ordinary one, evaluated on the LIVE branch.
//
// Build:
//   c++ -std=c++17 -O2 probe_specialize_deadbranch.cpp -I../../build/include \
//       -L../../build/src -lHalide -Wl,-rpath,../../build/src -o probe_specialize_deadbranch
// ============================================================================

static void banner(const char *s) { fprintf(stderr, "\n==================== %s ====================\n", s); }
static void run(const char *name, void (*fn)()) {
    banner(name);
    try { fn(); fprintf(stderr, "[OK] no error\n"); }
    catch (const Halide::Error &e) { fprintf(stderr, "[ERROR] %s\n", e.what()); }
}

// (a) Baseline: both producers at f.x, x present in both branches. Each
// producer appears only in the branch that uses it. Legal.
static void case_a() {
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Param<bool> cond; Var x("x"), y("y");
    Func g("g"), gc("gc"), f("f");
    g(x, y) = in(x, y); gc(x, y) = in(x, y);
    f(x, y) = select(cond, g(x, y), gc(x, y));
    f.compute_root(); f.specialize(cond);
    g.compute_at(f, x); gc.compute_at(f, x);
    Func out("out"); out(x, y) = f(x, y);
    out.print_loop_nest();
}

// (b) THE KEY CASE. f is tiled ONLY in the cond-true branch, so the tile loop
// `xi` exists there but NOT in the fallback. g is alive ONLY in that branch
// (cond true -> f = g), and g.compute_at(f, xi) names the tile loop. The
// fallback -- where g is DEAD -- has no `xi`. If dead branches imposed
// constraints this would fail; it does NOT. g is placed at xi in its live
// branch; the fallback (using gc at x) is unaffected.
static void case_b() {
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Param<bool> cond; Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
    Func g("g"), gc("gc"), f("f");
    g(x, y) = in(x, y); gc(x, y) = in(x, y);
    f(x, y) = select(cond, g(x, y), gc(x, y));
    f.compute_root();
    f.specialize(cond).tile(x, y, xo, yo, xi, yi, 4, 4);  // xi exists only here
    g.compute_at(f, xi);   // g alive here; xi absent from the fallback (g dead there)
    gc.compute_at(f, x);   // gc alive in the fallback, where x exists
    Func out("out"); out(x, y) = f(x, y);
    out.print_loop_nest();
}

// (c) The genuine (non-dead-branch) error, for contrast. g.compute_at(f, x),
// but the branch where g is ALIVE (cond true) tiled x away, so x does not exist
// there. This fails -- and the error lists the LIVE branch's loops (xo/yo/xi/yi)
// as the legal set, confirming the check is evaluated on the live branch, not
// the fallback (which still has x). The var must exist where the producer LIVES.
static void case_c() {
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Param<bool> cond; Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
    Func g("g"), gc("gc"), f("f");
    g(x, y) = in(x, y); gc(x, y) = in(x, y);
    f(x, y) = select(cond, g(x, y), gc(x, y));
    f.compute_root();
    f.specialize(cond).tile(x, y, xo, yo, xi, yi, 4, 4);  // branch A: x is tiled away
    g.compute_at(f, x);    // g alive in branch A, but x no longer exists there
    gc.compute_at(f, x);
    Func out("out"); out(x, y) = f(x, y);
    out.print_loop_nest();
}

int main() {
    run("(a) baseline: both at x (both branches have x)", case_a);
    run("(b) g at a loop that exists ONLY in g's live branch (dead fallback lacks it)", case_b);
    run("(c) contrast: g at x, but g's LIVE branch tiled x away", case_c);
    return 0;
}

// ---------------------------------------------------------------------------
// VERIFIED RESULTS (Halide 22):
//
// (a) [OK]  g under branch A, gc under the fallback, both at x. Fine.
//
// (b) [OK]  g is placed at the tile-inner xi in its live (cond-true) branch;
//           the fallback uses gc at x. The fallback LACKING xi is irrelevant --
//           g was pruned from it. So a producer imposes no requirement on the
//           branch where it is dead.  Loop nest:
//             produce f:
//               for y.yo: for x.xo: for y.yi: for x.xi:
//                 produce g: g(...) consume g: f(...)          # branch A (g)
//               for y: for x:
//                 produce gc: gc(...) consume gc: f(...)       # fallback (gc)
//             consume f: produce out: for y: for x: out(...)
//
// (c) [ERROR] "Func \"g\" is computed at the following invalid location:
//               g.compute_at(f, x);
//             Legal locations for this function are:
//               g.compute_root();  g.compute_at(f, Var::outermost());
//               g.compute_at(f, yo);  g.compute_at(f, xo);
//               g.compute_at(f, yi);  g.compute_at(f, xi);"
//           The legal set is branch A's TILE loops -- the check is on the branch
//           where g LIVES, which tiled x away. Nothing about the fallback.
//
// TAKEAWAY: dead-branch elimination is EARLY (simplify_specializations, before
// schedule_functions). Scheduling on the dead side does not error; the schedule
// only has to be valid where the producer is actually used. (Func.h's "the Var
// must exist in all paths" means all paths where the producer is USED -- with
// select, that is just its one live branch.)
// ---------------------------------------------------------------------------
