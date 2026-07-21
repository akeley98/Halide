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
// A broken producer/consumer relationship. `f` is read by TWO Funcs that are
// both computed at root: `g` and `output` (which reads `f` directly as well as
// through `g`). Computing `f` inside `g`'s loops makes `f`'s values available
// only within `g` -- but `output` also needs `f`, at the root level, where the
// values no longer exist.
//
// Halide reports that, because `f` is used in more than one place, the only
// legal location is `compute_root` (the one loop level that encloses every
// use). This is the canonical case that a wrapper Func (`f.in(...)`, a later
// milestone) is used to fix; here it simply must be rejected.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    f(x, y) = in(x, y);

    Func g("g");
    g(x, y) = f(x, y);
    g.compute_root();

    Func output("output");
    output(x, y) = f(x, y) + g(x, y); // reads f directly AND via g

    f.compute_at(g, x); // f only available inside g; output can't see it -> illegal

    output.print_loop_nest();
}
