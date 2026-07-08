#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Realization order is a POST-ORDER DFS from the output, NOT a global sort by
// name (§6). This example distinguishes the two:
//
//   out(x) = f(x) + keep(x);   // out's callees, sorted: [f, keep]  (f < keep)
//   f(x)   = mid(x) + 3;       // f  reads mid
//   mid(x) = x * 2;
//   keep(x)= a(x) * 5;         // keep is INLINE; reads `a`
//   a(x)   = x + 1;            // `a` is alphabetically FIRST
//
// A *global* name-keyed topological sort would realize `a` early -- it is a
// leaf and alphabetically first -- giving order a, mid, f, out.
//
// Real Halide instead does a DFS from `out`: it descends `out`'s callee list in
// sorted order [f, keep], so it fully realizes f's subtree (mid, f) BEFORE it
// ever reaches `a` (which is gated behind `keep`, and keep > f). The post-order
// realization is therefore:  mid, f, a, out.  `a` is realized AFTER f even
// though "a" < "f" -- the alphabetical key only orders each node's *direct
// callee list*; the global order is the DFS post-order.
int main() {
    Var x("x");
    Func a("a"), mid("mid"), f("f"), keep("keep"), out("out");
    a(x) = x + 1;
    mid(x) = x * 2;
    f(x) = mid(x) + 3;
    keep(x) = a(x) * 5;      // left inline
    out(x) = f(x) + keep(x);
    a.compute_root();
    mid.compute_root();
    f.compute_root();
    out.print_loop_nest();
    return 0;
}
