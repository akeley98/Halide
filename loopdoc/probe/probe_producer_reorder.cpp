#include "Halide.h"
#include <cstdio>
using namespace Halide;
static void banner(const char *s){fprintf(stderr,"\n==== %s ====\n",s);}
int main(){
    ImageParam in(type_of<uint8_t>(),2,"in");
    banner("producer at x; specialization reorders so x moves to outer");
    Var x("x"),y("y");
    Func g("g"),f("f");
    g(x,y)=in(x,y);
    f(x,y)=g(x,y)+g(x+1,y);
    f.compute_root();
    f.specialize(Param<bool>()).reorder(y,x);   // specialized: innermost-first [y,x] -> for x { for y }
    g.compute_at(f,x);
    Func out("out"); out(x,y)=f(x,y);
    out.print_loop_nest();
    return 0;
}
