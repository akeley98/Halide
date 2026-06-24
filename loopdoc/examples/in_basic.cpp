#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// f.in(g): an identity wrapper, scheduled compute_root. g reads f_in_g; f_in_g
// reads f. Realization order f -> f_in_g -> g.
int main(){
    Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func f("f"); f(x,y)=in(x,y);
    Func g("g"); g(x,y)=f(x,y)+f(x+1,y);
    Func fw=f.in(g);
    f.compute_root(); fw.compute_root(); g.compute_root();
    g.print_loop_nest();
}
