#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// f.in(): one GLOBAL wrapper used by EVERY consumer (g1, g2, g3 all redirected).
// f is read only by the global wrapper.
int main(){
    Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func f("f"); f(x,y)=in(x,y);
    Func g1("g1"); g1(x,y)=f(x,y);
    Func g2("g2"); g2(x,y)=f(x,y);
    Func g3("g3"); g3(x,y)=f(x,y);
    Func out("out"); out(x,y)=g1(x,y)+g2(x,y)+g3(x,y);
    Func fg=f.in();
    f.compute_root(); fg.compute_root();
    g1.compute_root(); g2.compute_root(); g3.compute_root();
    out.print_loop_nest();
}
