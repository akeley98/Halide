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
// compute_at host is not a consumer. `g` is computed at root and has real
// loops, but it never reads `f`. You cannot compute `f` inside `g`'s loops
// because `g` has no point at which it needs `f`. The only Func that reads `f`
// is `output`, which is not inside `g`. Halide lists legal locations only in
// terms of `output` (the real consumer), not `g`.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);

    Func g("g");
    g(x, y) = in(x, y); // g does NOT read f
    g.compute_root();

    Func output("output");
    output(x, y) = f(x, y) + g(x, y);

    f.compute_at(g, y); // g is not a consumer of f -> illegal

    output.print_loop_nest();
}
