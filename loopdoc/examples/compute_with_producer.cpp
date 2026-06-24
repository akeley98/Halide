#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// A producer computed at the fused level must name the PARENT (f), because the
// shared loop belongs to the parent. `input` is read by both f and g and is
// computed at f's (fused) y loop, landing inside the shared loop, wrapping both
// members' x loops.
//
//   produce f:
//     produce g:
//       for fused.y:
//         produce input:
//           for y: for x: input(...) = ...
//         consume input:
//           for x: f
//           for x: g
//   consume f: consume g: produce h: ...
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
    input.compute_at(f, y);
    h.print_loop_nest();
}
