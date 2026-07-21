#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// POSITIVE counterpart to neg_compute_at_two_consumers: a wrapper is the fix.
// output reads f directly AND via g, so f.compute_at(g,x) would be illegal (f
// also used at root by output). Giving g its own wrapper f.in(g) -- a Func with
// a SINGLE consumer -- lets that wrapper be computed inside g, while output
// keeps reading f directly.
int main(){
    Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func f("f"); f(x,y)=in(x,y);
    Func g("g"); g(x,y)=f(x,y);
    Func output("output"); output(x,y)=f(x,y)+g(x,y);
    Func fw=f.in(g);
    f.compute_root(); fw.compute_at(g,x); g.compute_root();
    micro_halide_collapses(fw,{x,y}); // wrapper is a single point at g's x loop
    output.print_loop_nest();
}
