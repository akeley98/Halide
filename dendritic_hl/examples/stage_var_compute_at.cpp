#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§7 legality): compute_at(f, v) where v exists in only SOME stages
// of the host f, and the producer is read only by a stage that HAS v -> legal.
//
// f's pure stage f(x, y) loops over [x, y]. Its update f(x, 0) += ... writes
// only row y = 0, so that stage's free vars are just [x] (the y slot is the
// constant 0 -- no y loop). p is read ONLY by the pure stage, which has y, so
// p.compute_at(f, y) is legal: p is injected into the pure stage's y loop, and
// the update stage (which never reads p) gets nothing.
//
//   produce f:
//     for y:                 # pure stage: has y
//       produce p:
//         for x: p(...) = ...
//       consume p:
//         for x: f(...) = ...
//     for x:                 # update stage: only x (y is the constant 0)
//       f(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x) = in(x, 0);

    Func f("f");
    f(x, y) = p(x);     // pure stage reads p, has y
    f(x, 0) += in(x, 1); // update stage writes row 0; free var x only; does NOT read p

    p.compute_at(f, y); // legal: only the pure stage reads p, and it has a y loop

    f.print_loop_nest();
}
