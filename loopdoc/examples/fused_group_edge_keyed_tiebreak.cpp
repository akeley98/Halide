#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A fused group is one node in realization order, but the tie-break key that
// orders it against a consumer's OTHER producers is the key of the specific
// member that consumer reads -- it is carried on the edge, not deducible from
// the group (loopdoc.md §6 / §14).
//
// Group {a, z} (a.compute_with(z, x)); mm is a non-member with a < mm < z.
// `c1` reads one member + mm; `c2` reads the other member. The two runs differ
// ONLY in which member c1 reads, yet the group flips sides of mm:
//   c1 reads a (a < mm): the group is reached first -> group, then mm
//   c1 reads z (mm < z): mm is reached first        -> mm, then group
// (The printf labels go to stdout; print_loop_nest goes to stderr, which is what
// the harness compares -- so the captured output is just the two nests.)
static void run(const char *label, bool c1_reads_z) {
    Var x("x");
    ImageParam in(type_of<int>(), 1, "in");
    Func a("a"); a(x) = in(x);
    Func z("z"); z(x) = in(x) + 1;
    Func mm("mm"); mm(x) = in(x) + 2;
    Func c1("c1"), c2("c2");
    if (!c1_reads_z) { c1(x) = a(x) + mm(x); c2(x) = z(x); }   // c1 reads member a
    else             { c1(x) = z(x) + mm(x); c2(x) = a(x); }   // c1 reads member z
    Func out("out"); out(x) = c1(x) + c2(x);
    a.compute_root(); z.compute_root(); mm.compute_root();
    c1.compute_root(); c2.compute_root();
    a.compute_with(z, x);
    printf("=== %s ===\n", label);
    out.print_loop_nest();
}

int main() {
    run("c1 reads member a", false);
    run("c1 reads member z", true);
    return 0;
}
