#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// NEGATIVE (rfactor x update(1) x indirect use): h.compute_at(intm, u) is ILLEGAL
// when g is compute_root. f_intm uses h only through g; with g at root, h's only
// use sits inside g at the top level, NOT inside f_intm's u loop. So the level
// (intm, u) does not enclose h's use, and Halide rejects the schedule. (Contrast
// rfactor_indirect_at_intm, where g is at intm.u and the same h.compute_at
// becomes legal.)

int main()
{
    Var x("x"), y("y"), u("u");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func h("h");
    h(x, y) = in(x, y);
    Func g("g");
    g(x, y) = h(x, y) + h(x + 1, y);

    Func f("f");
    RDom ra(0, 8, "ra");
    RDom rb(0, 8, 0, 8, "rb");
    f(x) = 0;
    f(x) += in(x, ra);
    f(x) += g(rb.x, rb.y);

    f.update(0).reorder(ra, x);
    Func intm = f.update(1).rfactor(rb.y, u);
    intm.compute_root();
    g.compute_root();
    h.compute_at(intm, u); // ILLEGAL: g is at root, so h's use is not inside intm.u

    f.print_loop_nest();
}
