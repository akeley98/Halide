#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// All three storage/compute levels distinct and legal.
//
// g is stored at the output's y loop, computed at its x loop, and its
// allocation hoisted to the outermost level. The store level (y) differs from
// the compute level (x), so a `store g:` node appears at y; the hoist level
// (root) encloses the store level, so it is legal but invisible. The printed
// nest is therefore identical to store_at(output,y).compute_at(output,x) with
// no hoist -- hoist_storage_root adds nothing to the structure.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + g(x, y + 1) + g(x + 1, y + 1);

    g.store_at(output, y).compute_at(output, x).hoist_storage_root();

    output.print_loop_nest();
}
