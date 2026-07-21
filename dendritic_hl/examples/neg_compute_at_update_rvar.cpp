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
// The §8 legal-site rule spans ALL stages: a producer's compute site must
// enclose every use across every stage. Here p is read by BOTH f's pure stage
// (f(x) = p(x)) and f's update stage (f(x) += p(r)). The update stage's
// reduction loop `r` exists only in that stage, so it does not enclose the
// pure-stage use of p. Computing p there is illegal; the only sites enclosing
// both uses are the shared `x` loop and root.

int main()
{
    Var x("x");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = in(x, 0);

    Func f("f");
    f(x) = p(x);          // pure stage reads p
    RDom r(0, 16, "r");
    f(x) += p(r);         // update stage reads p

    p.compute_at(f, r);   // r is only in the update stage -> illegal

    f.print_loop_nest();
}
