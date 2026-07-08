#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Task 4 family: clone_in × specialize, all combinations of Choice A × Choice B
// (loopdoc.md §13/§15). `p` is cloned FOR TWO consumers via the list form
// p.clone_in({g, h}); g is pure and reads the clone pointwise, h is impure and
// reads the clone in an update (a stencil reduction). The ORIGINAL p is kept
// alive and non-trivially used by `keep` (so both p and the clone pc realize and
// we can see which one carries a specialization).
//
// choiceA (WHAT is specialized, relative to the clone):
//   0 — specialize p BEFORE clone_in  (the clone should DEEP-COPY p's spec)
//   1 — clone_in, then specialize the ORIGINAL p  (only p gets branches)
//   2 — clone_in, then specialize the CLONE pc     (only pc gets branches)
// The specialization splits x, so "has branches" is structurally visible.
//
// choiceB (how g, h, and the clone are scheduled):
//   0 — g and h fused with compute_with
//   1 — g and h are producers of f; the clone pc is compute_at(f, y)
//   2 — like 1, but h is rfactor'd so the rfactor intermediate consumes pc
//   3 — ILLEGAL (negative): pc compute_at g's inner loop, but h also reads pc
//       outside g, so pc cannot enclose all its uses.
[[nodiscard]] int main_impl(int choiceA, int choiceB) {
    Var x("x"), y("y"), xo("xo"), xi("xi"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    RDom r(0, 4, 0, 4, "r");
    Param<bool> cond;

    Func p("p");
    p(x, y) = cast<int32_t>(in(x, y));          // original (pure)
    p.compute_root();                            // realized (kept by `keep`); also required to specialize it

    if (choiceA == 0) p.specialize(cond).split(x, xo, xi, 4);   // specialize BEFORE clone

    Func g("g"), h("h");
    g(x, y) = p(x, y) + 1;                        // pure consumer of the clone
    h(x, y) = 0;
    h(x, y) += p(x + r.x, y + r.y);               // impure consumer: stencil reduction over the clone

    Func pc = p.clone_in({g, h});                 // g and h now read the clone

    if (choiceA == 1) p.specialize(cond).split(x, xo, xi, 4);   // specialize the ORIGINAL after clone
    if (choiceA == 2) pc.specialize(cond).split(x, xo, xi, 4);  // specialize the CLONE after clone

    Func keep("keep");
    keep(x, y) = p(x, y) * 2;                     // non-trivial use of the original p

    Func f("f"), out("out");
    f(x, y) = g(x, y) + h(x, y);
    f.compute_root();

    if (choiceB == 0) {                           // compute_with: fuse g into h
        g.compute_root();
        h.compute_root();
        g.compute_with(h, x);
        pc.compute_root();
    } else if (choiceB == 1) {                    // producers of f; clone compute_at f.y
        g.compute_at(f, y);
        h.compute_at(f, y);
        pc.compute_at(f, y);
        // g and h are computed at f.y, so their own y is a single point and
        // elides (declared, §7). pc keeps its y (the stencil in h reads a y
        // range). h's update stage collapses y too.
        micro_halide_collapses(g, {y});
        micro_halide_collapses(h, {y});
        micro_halide_collapses(h.update(0), {y});
    } else if (choiceB == 2) {                    // + h rfactor'd; the intermediate consumes pc
        // FLAGGED / not exercised by a committed .cpp (main_agent_to_human.md):
        // clone_in({g,h}) then rfactor(h) breaks the wrap -- after rfactor, h
        // calls hintm (not p), so Halide errors "h does not call p". Making the
        // rfactor output consume the clone needs clone-AFTER-rfactor targeting
        // {g, hintm}, which conflicts with this family's clone-then-schedule
        // order. Left here to document the intended combo + the finding.
        g.compute_at(f, y);
        h.compute_at(f, y);
        Func hintm = h.update(0).rfactor(r.y, u);
        hintm.compute_at(f, y);
        pc.compute_at(f, y);
    } else {                                      // ILLEGAL: pc at g.x, but h reads pc too
        g.compute_at(f, y);
        h.compute_at(f, y);
        pc.compute_at(g, x);
    }

    out(x, y) = f(x, y) + keep(x, y);
    out.print_loop_nest();
    return 0;
}
