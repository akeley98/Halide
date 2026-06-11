#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// NEGATIVE example (must fail to compile a loop nest, in both Halide and
// micro_halide).
//
// compute_at names a loop that does not exist. `w` is never a dimension of
// `output`, so there is no `output` loop over `w` to compute `f` inside of.
// Halide reports an "invalid location" and lists the legal ones (root, and
// output's actual loops). The schedule is rejected before any loop nest is
// produced.

int main()
{
    Var x("x"), y("y"), w("w");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);

    Func output("output");
    output(x, y) = f(x, y) + f(x, y + 1);

    f.compute_at(output, w); // w is not a loop of output -> illegal

    output.print_loop_nest();
}
