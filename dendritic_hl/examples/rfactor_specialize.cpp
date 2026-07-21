#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// rfactor x specialize (loopdoc.md sections 12, 15). rfactor CREATES a new
// intermediate Func (f_intm) with its own partial-reduction update stage, and
// REWRITES f's update into a merge. You can then specialize the functions rfactor
// produced -- here the intermediate's partial-reduction update stage. Its
// specialization list attaches to that stage only (section 15's per-stage
// affinity), so f_intm's init stays one nest, its partial stage splits into a
// (split-branch, fallback) pair, and the merge f is untouched. The specialized
// branches interleave correctly inside the rfactor'd realization (intermediate
// realized at root, then f).
//
// Verified against real Halide:
//   produce f_intm:
//     for u: for x: f_intm(...)=...                      # init (unspecialized)
//     for x.xo: for x.xi: for u: for r: f_intm(...)=...  # partial BRANCH (split x)
//     for x: for u: for r: f_intm(...)=...               # partial FALLBACK
//   consume f_intm:
//     produce f:
//       for x: f(...)=...                                # f init
//       for x: for r: f(...)=...                         # f merge (over r.y)
int main() {
    Var x("x"), u("u"), xo("xo"), xi("xi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f");
    RDom r(0, 10, 0, 10, "r");
    f(x) = 0;
    f(x) += in(r.x, r.y);                       // 2-D sum reduction
    Func intm = f.update(0).rfactor(r.y, u);    // factor r.y -> new Func f_intm
    intm.compute_root();
    Param<bool> cond;
    intm.update(0).specialize(cond).split(x, xo, xi, 4);  // specialize the partial stage
    f.print_loop_nest();
}
