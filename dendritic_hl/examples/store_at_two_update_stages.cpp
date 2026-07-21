#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// ADVERSARIAL (§8 per host stage): a producer used in TWO of the host's stages
// with store_at must get a `store` node in BOTH -- not just one. p is read by
// f's update(0) and update(1) (but not the pure stage); p.store_at(f, y) puts a
// `store p:` node at y in each update stage, none in the pure stage. Guards
// against a fix that files the store node in only a single stage.
//
//   produce f:
//     for y: for x: f               # pure stage: NO store node
//     for y: store p: for x: produce p ... consume p: f   # update 0
//     for y: store p: for x: produce p ... consume p: f   # update 1

int main()
{
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func p("p");
    p(x, y) = in(x, y);

    Func f("f");
    f(x, y) = 0;            // pure stage: no p
    f(x, y) += p(x, y);     // update 0 reads p
    f(x, y) += p(x, y) * 2; // update 1 reads p

    p.compute_at(f, x).store_at(f, y);
    micro_halide_collapses(p, {x, y}); // p(x,y) is a single point at f's x loop

    f.print_loop_nest();
}
