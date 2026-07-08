// Probe: is realization order a GLOBAL name sort, or a post-order DFS from the
// output with each node's callee list locally sorted?
// out(x) = f(x) + keep(x);  f reads mid reads a_leaf;  keep (inline) reads `a`.
// `a` is alphabetically FIRST. A global name-topological sort would realize `a`
// early (it is a leaf). A DFS-from-out would descend f (f<keep) fully first,
// only reaching `a` via keep afterwards -> `a` realized AFTER f.
#include "Halide.h"
using namespace Halide;
int main() {
    Var x("x");
    Func a("a"), f("f"), mid("mid"), keep("keep"), out("out");
    a(x) = x + 1;
    mid(x) = x * 2;
    f(x) = mid(x) + 3;
    keep(x) = a(x) * 5;          // keep is inline; reads a
    out(x) = f(x) + keep(x);
    a.compute_root();
    f.compute_root();
    mid.compute_root();
    // keep left inline
    out.print_loop_nest();
    return 0;
}
