// Probe: two DISTINCT wrappers of one producer, scheduled at DIFFERENT loop
// levels, with stencils + tiling chosen so no wrapper loop collapses to [0,1]
// (so the two placements are fully visible in the nest). This is the positive
// counterpart to the collision probe: here `a` and `b` each call `common1`
// DIRECTLY, so the two in() calls resolve to disjoint pin-sets {a} and {b} and
// build two separate wrappers, independently schedulable.
#include "Halide.h"
using namespace Halide;
#include <stdio.h>
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
    ImageParam in(type_of<int>(), 2, "in");
    Func common1("common1");
    common1(x, y) = in(x, y) + in(x + 1, y + 1);
    Func a("a"), b("b");
    a(x, y) = common1(x, y) + common1(x + 1, y) + common1(x, y + 1);   // 2-D stencil
    b(x, y) = common1(x, y) + common1(x + 2, y) + common1(x, y + 2);   // wider stencil
    Func out("out");
    out(x, y) = a(x, y) + b(x, y);

    Func wa = common1.in(a);   // pins a -> wrapper wa
    Func wb = common1.in(b);   // pins b -> wrapper wb (disjoint key -> distinct wrapper)

    // Tile both consumers; place each wrapper at a DIFFERENT level of its own
    // consumer. Tile factors >= 4 with stencil halos keep every wrapper loop
    // extent > 1 (no [0,1] collapse).
    a.compute_root().tile(x, y, xo, yo, xi, yi, 8, 8);
    b.compute_root().tile(x, y, xo, yo, xi, yi, 8, 8);
    wa.compute_at(a, xo);      // wa inside a's inner tile loops
    wb.compute_at(b, yo);      // wb at b's OUTER tile row  -- a different level
    common1.compute_root();
    out.print_loop_nest();
    return 0;
}

// FINDING (2026-07-08): real Halide builds TWO distinct wrappers
// (common1_in_a, common1_in_b) because a and b directly call common1, so the
// pins are the disjoint keys {a} and {b}. Each wrapper is independently placed:
// wa nests inside a's tile (produce common1_in_a under a's xi/yi), wb sits at
// b's outer row (produce common1_in_b under b's yo, above b's xi/yi). No wrapper
// loop is [0,1]. Confirms the "per-consumer staging point at its own level"
// claim in loopdoc §13, and that DISJOINT pin-sets yield independent wrappers
// (contrast probe_in_key_set_collision.cpp, where overlapping-unequal sets do
// NOT).
