#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// An UNSCHEDULED in() wrapper is pure + inline, so it is substituted away: the
// nest is just f -> g, exactly as if the in() were not there.
int main(){
    Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func f("f"); f(x,y)=in(x,y);
    Func g("g"); g(x,y)=f(x,y)+f(x+1,y);
    Func fw=f.in(g);              // not scheduled -> inlines back
    f.compute_root(); g.compute_root();
    g.print_loop_nest();
}
