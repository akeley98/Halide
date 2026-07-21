#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE: you cannot compute_with two Funcs that have a producer/consumer
// dependency. Here g reads f, so g.compute_with(f, y) is illegal -- Halide
// errors "Invalid compute_with: there is dependency between f and g".
int main() {
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), g("g"), h("h");
    f(x, y) = in(x, y);
    g(x, y) = f(x, y) + 1;   // g depends on f
    h(x, y) = g(x, y);
    f.compute_root();
    g.compute_root();
    g.compute_with(f, y);    // illegal: dependency between fused Funcs
    h.print_loop_nest();
}
