#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// Realization-order tie-break SECONDARY key (so far untested: prior examples all
// used distinct name prefixes). b1 and b2 share the prefix "b" (trailing digits
// stripped), so the primary key ties and the *first-visitation order* decides --
// NOT the alphabetical full name. output lists b2 before b1, so b2 is visited
// (and realized) first, even though "b1" < "b2" alphabetically. Made observable
// by structural difference: b2 is 2-D (two loops), b1 is 1-D (one loop), so the
// first produce has two loops.
int main(){
    Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
    Func b1("b1"); b1(x)=in(x,0);       // 1-D
    Func b2("b2"); b2(x,y)=in(x,y);     // 2-D
    Func output("output"); output(x,y)=b2(x,y)+b1(x); // b2 first in RHS
    b1.compute_root(); b2.compute_root();
    output.print_loop_nest();
}
