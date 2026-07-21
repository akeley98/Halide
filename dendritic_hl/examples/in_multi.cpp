#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// f.in({g1,g2}): ONE shared wrapper for several named consumers. Both g1 and g2
// read the single wrapper f_in (contrast f.in(g1) + f.in(g2), which would make
// TWO separate wrappers). Realization f -> f_in -> g1 -> g2 -> out.
int main(){
    Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func f("f"); f(x,y)=in(x,y);
    Func g1("g1"); g1(x,y)=f(x,y);
    Func g2("g2"); g2(x,y)=f(x,y);
    Func out("out"); out(x,y)=g1(x,y)+g2(x,y);
    Func fw=f.in({g1,g2});
    f.compute_root(); fw.compute_root(); g1.compute_root(); g2.compute_root();
    out.print_loop_nest();
}
