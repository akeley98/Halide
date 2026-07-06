#include "Halide.h"
#include <cstdio>
using namespace Halide;

// ============================================================================
// Can a PRODUCER be scheduled differently depending on which specialized branch
// of its consumer uses it?  (User "case 2".)
//
// Short answer: NOT with any scheduling directive. specialize() forks only the
// specialized Func's OWN schedule; a producer is a separate Func with ONE
// schedule, shared by every branch of every consumer. The only per-branch
// variation a producer's *schedule* can show is placement (which loop its
// compute_at level resolves to), never its internal schedule (its own
// splits/tiles/compute-level).
//
// This file demonstrates, against real Halide (print_loop_nest -> stderr):
//   (1) clone_in(f)  -- one clone Func, ONE schedule, used in ALL f's branches.
//   (2) in(f)        -- same: one wrapper Func, ONE schedule, all branches.
//   (3) select(...)  -- the ONLY thing that gives per-branch producers, but it
//                       is an ALGORITHM construct, not a schedule. It changes
//                       WHAT f computes and carries NO equivalence guarantee
//                       between the two producers (Halide never checks g == gc).
//                       So it is a workaround that steps outside Halide's
//                       algorithm/schedule separation, not a scheduling answer.
//
// Build:
//   c++ -std=c++17 -O2 probe_specialize_case2.cpp -I../../build/include \
//       -L../../build/src -lHalide -Wl,-rpath,../../build/src -o probe_specialize_case2
// ============================================================================

static void banner(const char *s) { fprintf(stderr, "\n==================== %s ====================\n", s); }

// (1) clone_in: f reads a single clone gc; f is specialized into two
// structurally distinct branches (the specialized branch splits an outer loop).
// gc is tiled 4x4. RESULT: the one clone appears in BOTH branches, tiled
// IDENTICALLY -- there is no handle to tile it in one branch but not the other.
static void clone_case() {
    banner("(1) clone_in(f): one clone, one schedule, SAME in both branches");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Param<bool> cond;
    Var x("x"), y("y"), yo("yo"), yi("yi"), cxo("cxo"), cyo("cyo"), cxi("cxi"), cyi("cyi");
    Func g("g"), f("f");
    g(x, y) = in(x, y);
    f(x, y) = g(x, y);
    Func gc = g.clone_in(f);                          // f reads the clone
    f.compute_root();
    f.specialize(cond).split(y, yo, yi, 8);           // branch: extra outer loop
    gc.compute_at(f, x).tile(x, y, cxo, cyo, cxi, cyi, 4, 4);  // ONE schedule
    Func out("out");
    out(x, y) = f(x, y);
    out.print_loop_nest();
}

// (2) in: a wrapper (g stays in the pipeline). Same story: one wrapper Func,
// one schedule, present identically in every branch of f.
static void wrapper_case() {
    banner("(2) in(f): one wrapper, one schedule, SAME in both branches");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Param<bool> cond;
    Var x("x"), y("y"), yo("yo"), yi("yi"), wxo("wxo"), wyo("wyo"), wxi("wxi"), wyi("wyi");
    Func g("g"), f("f");
    g(x, y) = in(x, y);
    f(x, y) = g(x, y);
    Func gw = g.in(f);
    g.compute_root();
    f.compute_root();
    f.specialize(cond).split(y, yo, yi, 8);
    gw.compute_at(f, x).tile(x, y, wxo, wyo, wxi, wyi, 4, 4);
    Func out("out");
    out(x, y) = f(x, y);
    out.print_loop_nest();
}

// (3) select: TWO producers, g tiled and gc plain, chosen by the SAME condition
// f is specialized on. specialize prunes the select per branch (simplify), so
// branch A injects only g (tiled) and the fallback injects only gc (plain).
// This DOES give per-branch producers -- but note f's DEFINITION now names two
// Funcs and a runtime select. It is the algorithm that changed, and nothing
// verifies g and gc compute the same values (here they intentionally differ:
// gc = in + 1). That is the "no safety net" cost.
static void select_case() {
    banner("(3) select(cond, g, gc): per-branch producers, but ALGORITHM-level");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Param<bool> cond;
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
    Func g("g"), gc("gc"), f("f");
    g(x, y) = in(x, y);
    gc(x, y) = in(x, y) + 1;                          // NOT checked to equal g
    f(x, y) = select(cond, g(x, y), gc(x, y));        // <-- in the algorithm
    f.compute_root();
    f.specialize(cond);
    g.compute_at(f, x).tile(x, y, xo, yo, xi, yi, 4, 4);  // g tiled
    gc.compute_at(f, x);                                   // gc plain
    Func out("out");
    out(x, y) = f(x, y);
    out.print_loop_nest();
}

int main() {
    clone_case();
    wrapper_case();
    select_case();
    return 0;
}

// ---------------------------------------------------------------------------
// VERIFIED OUTPUT (Halide 22, print_loop_nest on stderr):
//
// (1) clone_in -- the single clone `g_clone_in_f` is tiled IDENTICALLY in both
//     the specialized (yo/yi-split) branch and the fallback:
//
//   produce f:
//     for y.yo:                         # specialized branch
//       for y.yi in [0, 7]:
//         for x:
//           produce g_clone_in_f:
//             for y.cyi in [0, 3]: for x.cxi in [0, 3]: g_clone_in_f(...) = ...
//           consume g_clone_in_f: f(...) = ...
//     for y:                            # fallback
//       for x:
//         produce g_clone_in_f:
//           for y.cyi in [0, 3]: for x.cxi in [0, 3]: g_clone_in_f(...) = ...
//         consume g_clone_in_f: f(...) = ...
//   consume f: produce out: for y: for x: out(...) = ...
//
//   => one clone, one tile; you cannot ask for the clone tiled in one branch
//      and plain in the other. (in(f) in case (2) is the same.)
//
// (3) select -- branch A produces a TILED g; the fallback produces a PLAIN gc:
//
//   produce f:
//     for y: for x:                     # branch A (cond true) -> f = g
//       produce g:
//         for y.yi in [0, 3]: for x.xi in [0, 3]: g(...) = ...
//       consume g: f(...) = ...
//     for y: for x:                     # fallback (cond false) -> f = gc
//       produce gc: gc(...) = ...
//       consume gc: f(...) = ...
//   consume f: produce out: for y: for x: out(...) = ...
//
//   => genuinely different producers with independent schedules per branch,
//      achieved by editing the algorithm (the select), not by scheduling.
// ---------------------------------------------------------------------------
