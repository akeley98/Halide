#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// NEGATIVE example (must fail in both Halide and micro_halide).
//
// `fuse` consumes its two input dimensions: after out.fuse(x, y, xy), out's
// dimension list is just [xy] -- x and y are no longer loops. Trying to compute
// g at out's (now nonexistent) x loop names a level that is not a current
// dimension, so the compute_at site is illegal. Only `xy` remains a legal site.
// This is the §8 "site must be a current loop" rule applied after a transform.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func out("out");
    out(x, y) = g(x, y);

    Var xy("xy");
    out.fuse(x, y, xy);
    g.compute_at(out, x); // x was fused away -> no such loop -> illegal

    out.print_loop_nest();
}
