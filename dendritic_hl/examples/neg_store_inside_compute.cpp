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
// The store level must ENCLOSE the compute level. Here storage is requested at
// the inner loop (x) while computation is at the outer loop (y) -- the buffer
// would be allocated inside the loop whose iterations are supposed to share it,
// which is impossible. Halide rejects it with an "invalid location" error.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + g(x, y + 1) + g(x + 1, y + 1);

    g.store_at(output, x).compute_at(output, y); // store (x) is INSIDE compute (y) -> illegal

    output.print_loop_nest();
}
