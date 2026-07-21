#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// store_root when the compute host is an intermediate Func (not the output).
//
// g is computed inside f (compute_at(f, y)) and f is itself compute_root; the
// output reads f. With g.store_root(), g's `store` node is the outermost node,
// wrapping the entire pipeline body -- f's produce/consume chain AND the
// output's -- even though only f uses g. This shows store_root allocates at the
// whole-pipeline scope, independent of where g is computed or used.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func f("f");
    f(x, y) = g(x, y) + g(x, y + 1);

    Func output("output");
    output(x, y) = f(x, y);

    f.compute_root();
    g.store_root().compute_at(f, y);

    output.print_loop_nest();
}
