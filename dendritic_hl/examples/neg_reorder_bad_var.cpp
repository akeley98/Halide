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
// `reorder` may only name dimensions that currently exist in the Func's
// dimension list. out has dimensions [x, y]; naming z -- which was never a
// dimension of out -- has no slot to permute, and Halide rejects the schedule
// ("could not find var z to reorder").

int main()
{
    Var x("x"), y("y"), z("z");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func out("out");
    out(x, y) = in(x, y);

    out.reorder(y, x, z); // z is not a dimension of out -> illegal

    out.print_loop_nest();
}
