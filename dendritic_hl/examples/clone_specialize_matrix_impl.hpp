#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Task 4 family: clone_in × specialize, all non-impossible combinations of
// Choice A × Choice B (loopdoc.md §13/§15). `p` is cloned for two consumers via
// the list form; `g` is pure and reads the clone pointwise, `h` is impure and
// reads the clone in a stencil-reduction update. The ORIGINAL `p` is kept alive
// and non-trivially used by `keep`.
//
// choiceA (what is specialized, relative to the clone):
//   0 — specialize p BEFORE clone_in  (the clone deep-copies p's spec)
//   1 — clone_in, then specialize the ORIGINAL p
//   2 — clone_in, then specialize the CLONE pc
// The specialization splits x, so "has branches" is structurally visible.
//
// choiceB (structure of g, h, and the clone):
//   0 — g and h fused with compute_with                              (positive)
//   1 — g and h producers of f; the clone pc is compute_at(f, y)     (positive)
//   2 — clone_in({g,h}) then rfactor(h): the rfactor moves h's read of the clone
//       into h_intm, so h no longer calls the wrapped Func           (NEGATIVE)
//   3 — corrected: rfactor(h) FIRST, so clone_in({g, h}) transitively
//       affects h_intm                                               (positive)
//   4 — pc compute_at(g, x) while h also reads pc: invalid location  (NEGATIVE)
[[nodiscard]] int main_impl(int choiceA, int choiceB) {
    Var x("x"), y("y"), xo("xo"), xi("xi"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    RDom r(0, 4, 0, 4, "r");
    Param<bool> cond;

    Func p("p");
    p(x, y) = cast<int32_t>(in(x, y));
    p.compute_root();
    if (choiceA == 0) p.specialize(cond).split(x, xo, xi, 4);   // specialize BEFORE clone

    Func g("g"), h("h");
    g(x, y) = p(x, y) + 1;                        // pure consumer of the clone
    h(x, y) = 0;
    h(x, y) += p(x + r.x, y + r.y);               // impure consumer: stencil reduction

    // choiceB==3: rfactor h BEFORE the clone, and clone for {g, h} which affects h_intm.
    Func h_intm;
    const bool rfactor_first = (choiceB == 3);
    if (rfactor_first) h_intm = h.update(0).rfactor(r.y, u);

    // This used to be Func pc = rfactor_first ? p.clone_in({g, h_intm}) : p.clone_in({g, h});
    // but it turns out all that mattered for distinguishing B2 (negative) and B3 (positive)
    // is just the relative order of the rfactor and clone_in.
    // h_intm need not be explicitly named; it will be correctly using the clone as long as the
    // clone_in happens after the rfactor, so clone_in's transitive search picks up h_intm.
    Func pc = p.clone_in({g, h});

    if (choiceA == 1) p.specialize(cond).split(x, xo, xi, 4);   // specialize the ORIGINAL after clone
    if (choiceA == 2) pc.specialize(cond).split(x, xo, xi, 4);  // specialize the CLONE after clone

    Func keep("keep");
    keep(x, y) = p(x, y) * 2;                     // non-trivial use of the original

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
        micro_halide_collapses(g, {y});
        micro_halide_collapses(h, {y});
        micro_halide_collapses(h.update(0), {y});
    } else if (choiceB == 2) {                    // NEGATIVE: clone then rfactor breaks the wrap
        g.compute_at(f, y);
        h.compute_at(f, y);
        pc.compute_at(f, y);
        (void)h.update(0).rfactor(r.y, u);        // h no longer calls the wrapped Func -> error
    } else if (choiceB == 3) {                    // corrected: rfactor output consumes the clone
        g.compute_at(f, y);
        h.compute_at(f, y);
        h_intm.compute_at(f, y);
        pc.compute_at(f, y);
        micro_halide_collapses(g, {y});
        micro_halide_collapses(h, {y});
        micro_halide_collapses(h.update(0), {y});
        micro_halide_collapses(h_intm, {y});
        micro_halide_collapses(h_intm.update(0), {y});
    } else {                                      // NEGATIVE: invalid pc location
        g.compute_at(f, y);
        h.compute_at(f, y);
        pc.compute_at(g, x);
    }

    out(x, y) = f(x, y) + keep(x, y);
    out.print_loop_nest();
    return 0;
}
