#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// ADVERSARIAL: clone_in of a NON-PURE Func. The clone copies ALL of f's stages
// (init + the r-reduction update), so f_clone_in_g prints both stages just like
// f. h keeps reading f; both f and the clone are realized.
int main(){
    Var x("x"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func f("f"); RDom r(0,8,"r");
    f(x)=0; f(x)+=in(x,r);          // non-pure
    Func g("g"); g(x)=f(x);
    Func h("h"); h(x)=f(x);
    Func out("out"); out(x)=g(x)+h(x);
    Func fc=f.clone_in(g);
    f.compute_root(); fc.compute_root(); g.compute_root(); h.compute_root();
    out.print_loop_nest();
}
