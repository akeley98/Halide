#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// NEGATIVE: `compute_with` requires the paired shared dimensions to have the
// SAME loop type (and device), not just the same name/count (loopdoc.md §17 /
// §14 legality). Here `f` types its x `parallel` and `g` types its x
// `vectorized`, then g fuses with f at x. Halide rejects this at schedule time:
//   "Invalid compute_with: for types of dim 0 of f.s0(x is parallel) and
//    g.s0(x is vectorized) do not match."
// The harness treats this as a negative (both micro and Halide must fail).

int main()
{
    Var x("x");
    ImageParam in(type_of<int>(), 1, "in");
    Func f("f"), g("g"), out("out");
    f(x) = in(x);
    g(x) = in(x) + 1;
    out(x) = f(x) + g(x);
    f.compute_root();
    g.compute_root();
    f.parallel(x);
    g.vectorize(x);
    g.compute_with(f, x);   // parallel vs vectorized on the fused dim -> error
    out.print_loop_nest();
}
