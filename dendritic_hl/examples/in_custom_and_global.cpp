#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// ADVERSARIAL: a custom wrapper AND a global wrapper of the same f coexist.
// g1 has a custom wrapper (custom takes precedence); g2 and g3 fall back to the
// global wrapper. Crucially BOTH wrappers read f -- the global wrapper does NOT
// redirect f's own (custom) wrapper. So f has two consumers (the two wrappers),
// they are siblings, and neither reads the other:
//   produce f:
//   consume f:
//     produce f_in_g1: ... (custom, reads f)   consumed by g1
//     produce f_in:    ... (global, reads f)   consumed by g2, g3
int main(){
    Var x("x"); ImageParam in(type_of<uint8_t>(),1,"in");
    Func f("f"); f(x)=in(x);
    Func g1("g1"); g1(x)=f(x);
    Func g2("g2"); g2(x)=f(x);
    Func g3("g3"); g3(x)=f(x);
    Func out("out"); out(x)=g1(x)+g2(x)+g3(x);
    Func wc=f.in(g1);   // custom wrapper for g1
    Func wg=f.in();     // global wrapper for the rest
    f.compute_root(); wc.compute_root(); wg.compute_root();
    g1.compute_root(); g2.compute_root(); g3.compute_root();
    out.print_loop_nest();
}
