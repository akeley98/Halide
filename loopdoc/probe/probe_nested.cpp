#include "Halide.h"
#include <cstdio>
using namespace Halide;
int main(){
    ImageParam in(type_of<uint8_t>(),2,"in");
    Var x("x"),y("y"),xi("xi"),yi("yi"),xii("xii");
    Func f("f"); f(x,y)=in(x,y);
    Param<bool> c1,c2;
    f.compute_root();
    f.specialize(c1).tile(x,y,xi,yi,4,4).specialize(c2).split(xi,xi,xii,2);
    Func out("out"); out(x,y)=f(x,y); out.print_loop_nest();
    return 0;
}
