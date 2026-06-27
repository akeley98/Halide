#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
//
// Shared body for a chain compute_with whose two fuse levels are at DIFFERENT
// depths. Three Funcs f, g, h, each 3-D (loop nest `for z: for y: for x`, z
// outermost), all compute_root, joined in a chain:
//
//     f.compute_with(g, <f_level>);    // f is g's child
//     g.compute_with(h, <g_level>);    // g is h's child  => members f, g, h
//
// The member order is child-before-parent f, g, h, so the SPINE OWNER is h
// (last). Only the spine owner keeps REAL shared loops; g (the middle func) is a
// child of h, so g's shared loops -- everything from the outermost down to g's
// own fuse level into h -- collapse to extent-1 scheduling points at g's slot
// inside h's real loops. g is ALSO f's parent, but it owns no real loops there.
//
// THE FINDING (why the level matters). "Splice the child into its parent's nest,
// sharing the loops from the outermost down to v" silently assumes v names a
// REAL loop in the splice target. That always holds for the spine owner, but a
// non-spine-owner parent (here g) only has real loops AT OR BELOW its own fuse
// level; everything above is a collapsed dummy at its slot. So the outcome turns
// on f's fuse level vs g's fuse level:
//
//   * f BELOW g  (compute_with_chain_inner:  f@y, g@z) -- well-behaved. g fused
//     into h at z, so g's y is a real loop; f fuses into g at y and shares that
//     real y. Nest:
//         for fused.z:
//           for fused.y: [ for x: h ]
//           for y: [ for x: g ; for x: f ]      # f shares g's real y
//
//   * f EQUAL g  (compute_with_chain_equal:  f@y, g@y) -- well-behaved (boundary
//     of the rule). All three bodies are siblings in the shared loops:
//         for fused.z:
//           for fused.y: [ for x: h ; for x: g ; for x: f ]
//
//   * f ABOVE g  (compute_with_chain_outer:  f@z, g@y) -- THE SURPRISE. f's fuse
//     level z resolves, in g, to g's COLLAPSED extent-1 z dummy, which sits at
//     g's slot deep inside h's real `for fused.y`. f does not actually share an
//     outer z loop; it splices at that dummy, and f's loops BELOW z (its own y,
//     x) RE-MATERIALIZE as real loops there -- so f is recomputed in full for
//     every iteration of the shared fused.y:
//         for fused.z:
//           for fused.y:
//             for x: h
//             for x: g
//             for y: [ for x: f ]               # f's own y, nested in fused.y
//
// THE RULE: a child's fuse level into its parent must be AT OR BELOW the parent's
// own fuse level (into the parent's parent). If it is ABOVE, the shared loop is a
// dummy and the child's sub-v loops re-materialize inside the spine owner's nest.
// The matching-loops precondition does NOT catch this (z exists by name in both f
// and g and the loop counts match), so Halide accepts it; the degeneracy shows up
// only in the built nest. The result is correct (f's values do not depend on the
// shared fused.y, so recomputing them is redundant, not wrong) but counter to
// what "fuse" suggests.
//
// Real Halide realizes a Pipeline{f, g, h}; micro cannot, so out = f + g + h is
// the single printed output. (As of writing, micro_halide does not reproduce the
// f-ABOVE-g re-materialization -- a documented gap, not a goal of this example.)
//
// level codes: 0 = z (outermost), 1 = y, 2 = x (innermost).
[[nodiscard]] int main_impl(int f_level, int g_level) {
    try {
        Var x("x"), y("y"), z("z");
        ImageParam in(type_of<uint8_t>(), 3, "in");
        Func f("f"), g("g"), h("h"), out("out");
        f(x, y, z) = in(x, y, z);
        g(x, y, z) = in(x, y, z);
        h(x, y, z) = in(x, y, z);
        out(x, y, z) = f(x, y, z) + g(x, y, z) + h(x, y, z);
        f.compute_root();
        g.compute_root();
        h.compute_root();
        auto V = [&](int k) { return k == 0 ? z : (k == 1 ? y : x); };
        f.compute_with(g, V(f_level));
        g.compute_with(h, V(g_level));
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
