#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Baseline for reorder_topological.cpp: the SAME pipeline with NO reorder.
//
// out's default dimension list is [x, y, z] (x innermost), so its loops are
// `for z: for y: for x:`. g is computed at out's y loop -- which still has out's
// x loop inside it -- so g's block sits two levels deep and a surviving `for x`
// appears inside both produce g and consume g. g itself needs only a row in x
// per (y,z) point of out, so its own y,z loops collapse and it emits `for x`.
//
// Compare reorder_topological.cpp, which reorders out so that y becomes the
// innermost loop: there the very same `g.compute_at(out, y)` lands at the
// deepest level, with no host loop left inside g's block. That structural
// difference is the only way a pure-serial reorder shows up in print_loop_nest.

int main()
{
    Var x("x"), y("y"), z("z");

    ImageParam in(type_of<uint8_t>(), 3, "in");

    Func g("g");
    g(x, y, z) = in(x, y, z);

    Func out("out");
    out(x, y, z) = g(x, y, z);

    g.compute_at(out, y);
    micro_halide_collapses(g, {y, z}); // g needs a full x row, one point in y,z

    out.print_loop_nest();
}
