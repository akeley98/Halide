#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE: the shared-callee gotcha. After f.clone_in(g), the callee p is read
// by BOTH f and the clone, so the only level enclosing both uses is root.
// p.compute_at(f, x) is therefore illegal (Halide lists both f and the clone as
// users of p). Contrast clone_basic, where p.compute_root() is legal.
int main(){
    Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func p("p"); p(x,y)=in(x,y);
    Func f("f"); f(x,y)=p(x,y);
    Func g("g"); g(x,y)=f(x,y);
    Func h("h"); h(x,y)=f(x,y);
    Func out("out"); out(x,y)=g(x,y)+h(x,y);
    Func fc=f.clone_in(g);
    f.compute_root(); fc.compute_root(); g.compute_root(); h.compute_root();
    p.compute_at(f, x);   // ILLEGAL: p shared by f and the clone
    out.print_loop_nest();
}
