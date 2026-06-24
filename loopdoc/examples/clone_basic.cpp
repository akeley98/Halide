#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// f.clone_in(g): g reads an independent clone of f; h keeps reading f. The clone
// duplicates f's definition but SHARES f's callee p (one produce p, read by both
// f and the clone). p.compute_root() makes the shared read legal.
int main(){
    Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func p("p"); p(x,y)=in(x,y);
    Func f("f"); f(x,y)=p(x,y);
    Func g("g"); g(x,y)=f(x,y);
    Func h("h"); h(x,y)=f(x,y);
    Func out("out"); out(x,y)=g(x,y)+h(x,y);
    Func fc=f.clone_in(g);
    p.compute_root(); f.compute_root(); fc.compute_root();
    g.compute_root(); h.compute_root();
    out.print_loop_nest();
}
