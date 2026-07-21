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
// Adversarial compute_inline x store level: compute_inline() resets the compute
// level back to inlined (loopdoc.md section 6) but does NOT clear a store level
// set earlier. So compute_root().store_root() followed by compute_inline() leaves
// f INLINE but still carrying a store level -- illegal by the same rule as
// neg_store_at_inlined, only reached via an inline OVERRIDE. This checks that
// compute_inline truly reset the level (else the store/inline check would not
// fire). Halide: "Func f is scheduled store_root(), but is inlined."

int main()
{
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f"), g("g"), output("output");
    f(x, y) = in(x, y);
    g(x, y) = f(x, y) + f(x + 1, y);
    output(x, y) = g(x, y);

    g.compute_root();
    f.compute_root().store_root();  // real store level while realized at root
    f.compute_inline();             // override back to inline; store level remains -> illegal

    output.print_loop_nest();
}
