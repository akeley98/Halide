#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Specializations are PER STAGE (loopdoc.md sections 1, 15): each Definition --
// the pure stage and each update stage -- carries its OWN specialization list.
// `f.update(n).specialize(...)` specializes only update stage n; the pure stage
// is untouched. This affinity is only visible on an IMPURE Func (one with an
// update), where the branches attach to one stage's nest and not the other's.
//
// Here f has a pure stage and one update stage. Only the UPDATE is specialized
// (tiled). Inside the single `produce f`, the pure stage prints as one plain
// nest, then the update stage prints as TWO concatenated nests (tiled branch +
// fallback). Verified against real Halide:
//   produce f:
//     for y: for x: f(...)=...                              <- pure stage (1 nest)
//     for y.yo: for x.xo: for y.yi: for x.xi: f(...)=...    <- update branch (tiled)
//     for y: for x: f(...)=...                              <- update fallback
//   consume f: produce out: for y: for x: out(...)=...
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);          // pure stage (stage 0) -- left unspecialized
    f(x, y) += in(x, y);         // update stage (stage 1)
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> cond;
    f.update(0).specialize(cond).tile(x, y, xo, yo, xi, yi, 4, 4);  // update only
    out.print_loop_nest();
}
