#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (positive complement of neg_split_stage_compute_at): the same
// per-stage split, but the producer is computed at a loop COMMON to both stages.
// f's update stage is split (x -> xo, xi) but y survives in both stages, so
// p.compute_at(f, y) is legal -- p is injected into both stages at their y loop:
//
//   produce f:
//     for y:                 # pure stage
//       produce p:
//         for x: p(...) = ...
//       consume p:
//         for x: f(...) = ...
//     for y:                 # update stage (split in x)
//       produce p:
//         for x: p(...) = ...
//       consume p:
//         for xo: for xi: f(...) = ...

int main()
{
    Var x("x"), y("y"), xo("xo"), xi("xi");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = in(x, 0);

    Func f("f");
    f(x, y) = p(x);
    f(x, y) += p(x);
    f.update(0).split(x, xo, xi, 8);

    p.compute_at(f, y); // legal: y survives in both stages, so it encloses both uses

    f.print_loop_nest();
}
