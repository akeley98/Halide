#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Per-stage specialization affinity, both stages (loopdoc.md sections 1, 15).
// The pure stage and the update stage are specialized INDEPENDENTLY, with
// different conditions and different transforms. Each stage's specialization
// list expands only that stage's nest, so `produce f` contains FOUR loop nests:
// the pure stage's (branch, fallback) then the update stage's (branch, fallback).
//
// Distinct conditions (cond1, cond2) so no specialization is re-fetched by a
// repeated Expr (loopdoc.md section 15 "Out of scope"). Both stages are
// scheduled, so there is no "update not scheduled" warning. Verified vs real
// Halide:
//   produce f:
//     for y.yo: for x.xo: for y.yi: for x.xi: f(...)=...   <- pure branch (cond1, tiled)
//     for y: for x: f(...)=...                              <- pure fallback
//     for y: for x.ux: for x.uxi: f(...)=...               <- update branch (cond2, split)
//     for y: for x: f(...)=...                              <- update fallback
//   consume f: produce out: for y: for x: out(...)=...
int main() {
    Var x("x"), y("y"), xo("xo"), yo("yo"), xi("xi"), yi("yi"), ux("ux"), uxi("uxi");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func f("f"), out("out");
    f(x, y) = in(x, y);          // pure stage (stage 0)
    f(x, y) += in(x, y);         // update stage (stage 1)
    out(x, y) = f(x, y);
    f.compute_root();
    Param<bool> cond1, cond2;
    f.specialize(cond1).tile(x, y, xo, yo, xi, yi, 4, 4);     // pure stage only
    f.update(0).specialize(cond2).split(x, ux, uxi, 8);       // update stage only
    out.print_loop_nest();
}
