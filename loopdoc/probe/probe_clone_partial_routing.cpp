// Probe: `clone_in(target)` redirects only the FIRST-direct-caller frontier on
// the paths down from `target` -- not "everything target computes". Uses
// compile_to_lowered_stmt because print_loop_nest hides the RHS (which clone_me
// variant each consumer actually loads is the whole point).
//
// DAG:  maybe_inline -> clone_me -> c1 -> c2 -> out
//                                   \--------> c2  (c2 ALSO reads clone_me directly)
//   clone_me.compute_root(); cloned.compute_root(); c2.compute_root();
//   c1.compute_at(c2, y).
//
// Run each clone_at, then grep the emitted /tmp/pcpr_<case>.stmt for which
// consumer's body loads `clone_me` (original) vs `clone_me..._clone_in_...`
// (clone). Both are always realized.
#include "Halide.h"
using namespace Halide;
#include <string>
static void run(const char *label, int clone_at) {
    Var x("x"), y("y");
    ImageParam in1(type_of<float>(), 2, "in");
    Func maybe_inline("maybe_inline"); maybe_inline(x, y) = in1(x, y) * 4;
    Func clone_me("clone_me"); clone_me(x, y) = maybe_inline(x, y) * 2 + maybe_inline(x + 1, y + 1);
    Func c1("c1"); c1(x, y) = clone_me(x, y + 1) + clone_me(x + 1, y);
    Func c2("c2"); c2(x, y) = clone_me(x, y + 1) + clone_me(x + 1, y) + c1(x, y) + c1(x + 1, y + 1);
    Func out("out"); out(x, y) = c2(x, y + 1) + c2(x + 1, y);
    Func cloned = clone_me.clone_in(clone_at == 0 ? out : clone_at == 1 ? c2 : c1);
    maybe_inline.in(cloned).compute_at(cloned, y);
    clone_me.compute_root(); cloned.compute_root(); c2.compute_root(); c1.compute_at(c2, y);
    out.compile_to_lowered_stmt(std::string("/tmp/pcpr_") + label + ".stmt", {in1}, Text);
}
int main() { run("out", 0); run("c2", 1); run("c1", 2); return 0; }

// FINDING (2026-07-08), from the lowered stmts:
//
//   clone_at=c1 : wrapper pinned on c1     -> c1 reads the CLONE;   c2's DIRECT
//                 reads use the ORIGINAL clone_me (c2 also gets the clone via c1)
//   clone_at=c2 : wrapper pinned on c2     -> c2's DIRECT reads use the CLONE;
//                 c1 reads the ORIGINAL clone_me (so c2's via-c1 reads are original)
//   clone_at=out: wrapper pinned on c2 too -> IDENTICAL to clone_at=c2. `out` does
//                 not call clone_me directly, so the walk descends to c2, stops at
//                 the first direct caller, and pins there. The clone is NAMED
//                 clone_me_clone_in_out but `out` itself is never modified.
//
// Consequences (the point):
//  1. "clone for target" != "everything target computes uses the clone." Only the
//     reads on the first-direct-caller frontier are redirected; anything reached
//     PAST that frontier (here c1, shadowed by c2's direct read) keeps the
//     original. `out` ends up fed by a MIX: clone via c2-direct, original via
//     c2->c1.
//  2. Shadowing: c2's DIRECT read of clone_me stops the walk at c2, so the deeper
//     c1 never gets the clone -- it WOULD have if c2 had no direct read.
//  3. Bleed-through: pinning on a shared Func rewrites its body, so non-named
//     consumers of that Func see the clone too (clone_at=c1 -> c2 reads the clone
//     via c1, unrequested).
//  4. The walk runs on the PRE-wrap call graph (find_direct_calls), i.e. it
//     ignores wrappers already registered -- see src_doc in_clone_in_transitivity.md.
