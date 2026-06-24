#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE: a producer at the fused level must be computed at the PARENT. The
// shared loop belongs to f (the parent); g (the child) does not own a fused
// loop, so input.compute_at(g, y) is illegal -- the legal site is
// input.compute_at(f, y).
int main() {
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func input("input"), f("f"), g("g"), h("h");
    input(x, y) = in(x, y);
    f(x, y) = input(x, y);
    g(x, y) = input(x, y) * 2;
    h(x, y) = f(x, y) + g(x, y);
    f.compute_root();
    g.compute_root();
    g.compute_with(f, y);
    input.compute_at(g, y);   // illegal: g is the child, not the parent
    h.print_loop_nest();
}
