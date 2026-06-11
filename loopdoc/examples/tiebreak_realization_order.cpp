#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Realization-order tie-break for `output = <producer> + <producer>`.
//
// When a consumer reads several producers scheduled at the same level, their
// relative realization order is NOT the left-to-right order of the defining
// expression. It is decided by name: alphabetical by name prefix (then by
// first-visitation order, then full name). See loopdoc.md section 4.
//
// Here the defining expression lists `b1d` first, but `a2d` (alphabetically
// earlier) is realized first. We make the two producers structurally different
// (2-D vs 1-D) so the ordering is actually visible after canonicalization:
// the first `produce` has two loops, the second has one.

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func a2d("a2d");
    a2d(x, y) = in(x, y);

    Func b1d("b1d");
    b1d(x) = in(x, 0);

    Func output("output");
    output(x, y) = b1d(x) + a2d(x, y); // RHS order: b1d, then a2d

    a2d.compute_root();
    b1d.compute_root();

    output.print_loop_nest();
}
