#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§11): the inline default of a non-pure Func read at DIFFERENT
// depths in different stages of its consumer -- the case that is NOT expressible
// as any single compute_at. p (itself non-pure) is left inline; f's pure stage
// reads p(x) (depth x) and f's update stage reads p(rf) (inside the rf loop).
// The inline default materializes p at the innermost point of EACH use,
// independently: inside f's x loop in stage 0, and inside f's rf loop in
// stage 1.
//
//   produce f:
//     for x:                  # f stage 0
//       produce p:            # p materialized at x
//         p(...) = ...
//         for rp:
//           p(...) = ...
//       consume p:
//         f(...) = ...
//     for x:                  # f stage 1
//       for rf:
//         produce p:          # p materialized inside rf (deeper than stage 0)
//           p(...) = ...
//           for rp:
//             p(...) = ...
//         consume p:
//           f(...) = ...

int main()
{
    Var x("x");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = in(x, 0);
    RDom rp(0, 4, "rp");
    p(x) += in(x, rp); // p is non-pure (has an update) -> cannot be substituted

    Func f("f");
    f(x) = p(x);       // f stage 0 reads p(x) at depth x
    RDom rf(0, 8, "rf");
    f(x) += p(rf);     // f stage 1 reads p(rf) inside the rf loop

    // p is realized at each use's innermost loop; p's own x is a single point
    // there, so it collapses in both of p's stages.
    micro_halide_collapses(p, {x});
    micro_halide_collapses(p.update(0), {x});

    f.print_loop_nest();
}
