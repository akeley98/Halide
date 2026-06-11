#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// store_root with compute_at: storage at the outermost level.
//
// g is computed per output scanline (compute_at(output, y)) but its storage is
// allocated at the very top (store_root). The `store g:` node is therefore the
// outermost node in the nest, wrapping the output's whole produce body. (This
// is the schedule from tutorial lesson 8 that enables sliding-window reuse of g
// across output scanlines -- an optimization that changes recomputation and
// buffer size but not the loop structure shown here.)

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func g("g");
    g(x, y) = in(x, y);

    Func output("output");
    output(x, y) = g(x, y) + g(x + 1, y) + g(x, y + 1) + g(x + 1, y + 1);

    g.store_root().compute_at(output, y);

    output.print_loop_nest();
}
