#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// The staging use: the wrapper is computed INSIDE its consumer (fw.compute_at(g,y)).
int main(){
    Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func f("f"); f(x,y)=in(x,y);
    Func g("g"); g(x,y)=f(x,y)+f(x+1,y);
    Func fw=f.in(g);
    f.compute_root(); fw.compute_at(g,y); g.compute_root();
    micro_halide_collapses(fw,{y}); // wrapper's y is a single point at g's y loop
    g.print_loop_nest();
}
