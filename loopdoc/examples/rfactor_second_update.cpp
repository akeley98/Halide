#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§12): rfactor a NON-FIRST update stage. f has two update stages;
// rfactor is called on update(1). Only that stage is factored/rewritten; the
// pure stage and update(0) are untouched, and f still prints all three of its
// stages in order inside one `produce f`. (The trivial reorder on update(0) only
// suppresses Halide's "some updates scheduled, others not" warning so the logs
// compare cleanly; it is an identity on a 1-D reduction.)
//
//   produce f_intm:                  # factors update(1)'s rb reduction
//     for u: for x:
//     for x: for u: for rb(=rb.x):
//   consume f_intm:
//     produce f:
//       for x:                        # pure stage
//       for x: for ra:                 # update(0), unchanged
//       for x: for rb(=rb.y):           # update(1) -> merge over rb.y

int main()
{
    Var x("x"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f");
    RDom ra(0, 8, "ra");
    RDom rb(0, 8, 0, 8, "rb");
    f(x) = 0;
    f(x) += in(x, ra);        // update 0: 1-D reduction over ra
    f(x) += in(rb.x, rb.y);   // update 1: 2-D reduction

    f.update(0).reorder(ra, x);              // identity; suppresses the warning
    Func intm = f.update(1).rfactor(rb.y, u); // factor the SECOND update
    intm.compute_root();

    f.print_loop_nest();
}
