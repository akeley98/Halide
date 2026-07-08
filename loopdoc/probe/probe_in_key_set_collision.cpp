// Probe: two in() calls whose resolved pin-sets OVERLAP but are not EQUAL.
//   common1 <- (leaf)
//   common2 <- common1              (shared intermediate)
//   mid     <- common1              (2nd direct caller of common1, only under out1)
//   out1    <- common2, mid         (reaches common1 via common2 AND via mid)
//   out2    <- common2              (reaches common1 only via common2)
//
//   common1.in(out1) resolves to {common2, mid}
//   common1.in(out2) resolves to {common2}
// The two calls share key "common2" but out1 also brings "mid". Does the second
// call reuse the first's wrapper, or does the partial overlap error? Test both
// orders. Real Halide only (a probe): print who reads what, or the error.
#include "Halide.h"
using namespace Halide;
#include <stdio.h>
#include <string>
static void run(const char *label, bool out2_first) {
    Var x("x");
    ImageParam in(type_of<int>(), 1, "in");
    Func common1("common1"); common1(x) = in(x);
    Func common2("common2"); common2(x) = common1(x) + common1(x + 1);
    Func mid("mid");         mid(x)     = common1(x) * 3;
    Func out1("out1");       out1(x)    = common2(x) + mid(x);
    Func out2("out2");       out2(x)    = common2(x) + common2(x + 1);
    Func out("out");         out(x)     = out1(x) + out2(x);
    try {
        Func w1, w2;
        if (!out2_first) { w1 = common1.in(out1); w2 = common1.in(out2); }
        else             { w2 = common1.in(out2); w1 = common1.in(out1); }
        printf("[%s] w1(out1)=%s  w2(out2)=%s  same=%d\n",
               label, w1.name().c_str(), w2.name().c_str(), w1.name() == w2.name());
        w1.compute_root(); w2.compute_root();
        common2.compute_root(); mid.compute_root(); common1.compute_root();
        out.compile_to_lowered_stmt(std::string("/tmp/pksc_") + label + ".stmt", {in}, Text);
    } catch (const CompileError &e)  { printf("[%s] CompileError: %s\n", label, e.what()); }
      catch (const InternalError &e) { printf("[%s] InternalError: %.80s\n", label, e.what()); }
}
int main() { run("out1_first", false); run("out2_first", true); return 0; }

// FINDING (2026-07-08): when the two calls resolve to OVERLAPPING but UNEQUAL
// pin-sets, ORDER MATTERS and is observable (unlike the equal-set case, which
// only changes the wrapper's name). get_wrapper keys the reuse decision on
// fs[0] alone; validate_wrapper (Func.cpp:2143) then requires the reused
// wrapper's existing consumer entries to line up EXACTLY with fs: every existing
// entry in fs must map to this wrapper, and every existing entry NOT in fs must
// NOT map to it. So:
//
//   out1 first ({common2,mid} -> w1), then out2 ({common2}):
//     reuse w1 for fs={common2}; but mid (not in fs) already shares w1
//     -> user_assert fails: "Redefinition of shared wrapper ... in common2 is
//        illegal since mid shares the same wrapper but is not part of the
//        redefinition"  => COMPILE ERROR.
//
//   out2 first ({common2} -> w2), then out1 ({common2,mid}):
//     reuse w2 for fs={common2,mid}; only existing entry is common2->w2 (== fs[0],
//     skipped) -> validate passes, returns w2. The extra key `mid` is NEVER
//     registered (the reuse branch does not add_wrapper), so mid SILENTLY keeps
//     reading the original common1. out1's request to wrap its mid-path is a
//     no-op.  => SUCCEEDS, but under-wraps.
//
// So "wrap order is free" holds only when the resolved pin-sets are EQUAL (then
// the 2nd call is a pure idempotent reuse). Unequal-but-overlapping sets are
// order-sensitive: subset-then-superset silently under-wraps; superset-then-
// subset is rejected.
