#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// compute_with records ONE fuse level per stage (loopdoc.md section 14 "The
// directive, and the state it records"): calling it again on the same stage
// OVERWRITES the previous edge -- it does NOT build a group {f, a, b}. Real
// Halide emits a "Warning:" to stderr when the edge is overwritten, then
// proceeds with the last edge only.
//
// Here f.compute_with(a, y) is overwritten by f.compute_with(b, y), so f fuses
// with b ONLY; a stays in its own nest. This is a POSITIVE example (it produces
// a valid loop nest) whose Halide run also prints a warning line -- used to
// exercise the harness's handling of "Warning:" output. Expected structure:
//
//   produce a:            # a: its own nest, not fused
//     for y: for x: a
//   consume a:
//     produce b:          # b is the fuse parent (spine owner), outermost
//       produce f:
//         for fused.y:     # b and f share this loop
//           for x: b
//           for x: f
//     consume b:
//       consume f:
//         produce out: ...
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func a("a"), b("b"), f("f"), out("out");
        a(x, y) = in(x, y);
        b(x, y) = in(x, y) + 1;
        f(x, y) = in(x, y) + 2;
        out(x, y) = a(x, y) + b(x, y) + f(x, y);
        a.compute_root();
        b.compute_root();
        f.compute_root();
        f.compute_with(a, y);   // recorded...
        f.compute_with(b, y);   // ...then OVERWRITTEN (Halide warns): f fuses with b only
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
