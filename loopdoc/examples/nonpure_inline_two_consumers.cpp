#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§11): the inline default of a non-pure Func read by TWO distinct
// consumers. p (non-pure, left inline) is read by both g and h, which are each
// compute_root. p is materialized independently inside EACH consumer, at that
// consumer's innermost use -- once in g, once in h:
//
//   produce g:
//     for x:
//       produce p: ... consume p: g(...) = ...
//   consume g:
//     produce h:
//       for x:
//         produce p: ... consume p: h(...) = ...
//     consume h:
//       produce out:
//         for x:
//           out(...) = ...

int main()
{
    Var x("x");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = 0;
    RDom rp(0, 4, "rp");
    p(x) += in(x, rp); // non-pure, left inline

    Func g("g");
    g(x) = p(x);
    Func h("h");
    h(x) = p(x) + 1;
    Func out("out");
    out(x) = g(x) + h(x);

    g.compute_root();
    h.compute_root();

    micro_halide_collapses(p, {x});           // p's x is a single point at each use
    micro_halide_collapses(p.update(0), {x});

    out.print_loop_nest();
}
