#include "Halide.h"
#include <cstdio>
using namespace Halide;
static void banner(const char *s){fprintf(stderr,"\n==== %s ====\n",s);}
int main() {
    ImageParam in(type_of<uint8_t>(), 2, "in");
    {   banner("inherit: tile BEFORE specialize -> both branches tiled; branch adds split");
        Var x("x"),y("y"),xi("xi"),yi("yi"),xii("xii");
        Func f("f"); f(x,y)=in(x,y); Param<bool> c;
        f.compute_root().tile(x,y,xi,yi,4,4);
        f.specialize(c).split(xi,xi,xii,2);
        Func out("out"); out(x,y)=f(x,y); out.print_loop_nest(); }
    {   banner("post-specialize directive applies to FALLBACK only");
        Var x("x"),y("y"),xi("xi");
        Func f("f"); f(x,y)=in(x,y); Param<bool> c;
        f.compute_root();
        f.specialize(c);           // fork schedule-so-far (just compute_root)
        f.split(x,x,xi,8);         // added AFTER specialize -> fallback only
        Func out("out"); out(x,y)=f(x,y); out.print_loop_nest(); }
    return 0;
}
