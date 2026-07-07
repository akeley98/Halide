#include "Halide.h"
#include <cstdio>
using namespace Halide;

// PROBE: what does rfactor do to specializations that were declared on the SAME
// update stage BEFORE the rfactor call? (User question / baffling case.)
//
// Model under test: a `Specialization` stores a COPY of the whole Definition --
// including the RHS VALUE expressions (the "algorithm"), not just the schedule
// (Definition.h: "the Expr in LHS/RHS may be different across specializations").
// specialize() forks that copy at call time; rfactor() then rewrites only the
// BASE definition into a merge (+ builds an intermediate), leaving the forked
// specialization's RHS frozen at the PRE-rfactor algorithm. So a specialized
// stage remembers the old algorithm.
//
// To make the "old algorithm" visible we use a distinctive RHS:
//     f(x) += in(r.x, r.y) * 100 + r.x
// The stale branch should read `in` directly with this exact math (a 2-D
// reduction, two r loops); the rfactor'd fallback should instead read the
// intermediate f_intm (a 1-D merge over r.y, one r loop). The full lowered Stmt
// (dumped to stmt/*.stmt.txt) shows the actual RHS in each branch.

static void banner(const char *s){ fprintf(stderr, "\n==================== %s ====================\n", s); }

int main() {
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Var x("x"), u("u"), xo("xo"), xi("xi");
    Param<bool> cond;

    Func f("f");
    RDom r(0, 10, 0, 10, "r");
    f(x) = 0;
    f(x) += cast<int32_t>(in(r.x, r.y)) * 100 + r.x;   // distinctive non-trivial RHS

    // (1) specialize the update stage FIRST (fork a copy of THIS definition)...
    f.update(0).specialize(cond).split(x, xo, xi, 4);
    // (2) ...then rfactor the same update stage (rewrites the BASE def only).
    Func intm = f.update(0).rfactor(r.y, u);
    intm.compute_root();

    banner("print_loop_nest (structure)");
    f.print_loop_nest();

    banner("dumping full lowered Stmt to stmt/spec_then_rfactor.stmt.txt");
    f.compile_to_lowered_stmt("stmt/spec_then_rfactor.stmt.txt", {in, cond}, Text);
    fprintf(stderr, "done\n");
    return 0;
}
