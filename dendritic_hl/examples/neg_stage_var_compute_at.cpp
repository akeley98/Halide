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
// compute_at(f, v) where v is missing from a stage that READS the producer.
// Here p is read by BOTH of f's stages, but the update stage f(x, 0) += p(x)
// writes only row y = 0, so it has no y loop. p.compute_at(f, y) is illegal: the
// update stage's use of p is not enclosed by any y loop. The only sites
// enclosing both uses are x and root.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = in(x, 0);

    Func f("f");
    f(x, y) = p(x);   // pure stage reads p, has y
    f(x, 0) += p(x);  // update stage reads p but has no y loop (y is constant 0)

    p.compute_at(f, y); // illegal: the update stage's use of p has no y to enclose it

    f.print_loop_nest();
}
