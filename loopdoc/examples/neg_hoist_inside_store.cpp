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
// The hoist-storage level must enclose the STORE level, not merely the compute
// level. Here g's storage is at the outermost level (store_root) and it is
// computed at the output's inner x loop, but the allocation is hoisted only to
// the output's y loop -- which is INSIDE store_root. Even though the hoist level
// (y) does enclose the compute level (x), it does not enclose the store level
// (root), so the schedule is illegal.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + g(x, y + 1) + g(x + 1, y + 1);

    g.store_root().compute_at(output, x).hoist_storage(output, y); // hoist inside store -> illegal

    output.print_loop_nest();
}
