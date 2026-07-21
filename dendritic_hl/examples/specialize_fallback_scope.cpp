#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// specialize scoping (loopdoc.md section 15): a directive issued on the Func
// AFTER a specialize() call modifies the parent (fallback) schedule only -- the
// already-forked specialization keeps the schedule it copied at fork time.
//
// Here specialize(cond) forks when f has only compute_root, so the specialized
// branch stays plain (2 loops). The split issued afterwards lands on the
// fallback (3 loops). Contrast specialize_inherit.cpp, where the directive came
// BEFORE specialize and so applied to both. Verified against real Halide:
//   produce f:
//     for y: for x:                   f(...)=...   <- specialization (forked early, plain)
//     for y: for x.x: for x.xi:       f(...)=...   <- fallback (got the later split)
int main() {
    Var x("x"), y("y"), xi("xi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> cond;
    f.specialize(cond);              // fork schedule-so-far (just compute_root)
    f.split(x, x, xi, 8);            // added AFTER specialize -> fallback only
    out.print_loop_nest();
}
