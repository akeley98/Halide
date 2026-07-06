#include "Halide.h"
#include <cstdio>
using namespace Halide;
static void banner(const char *s){fprintf(stderr,"\n==== %s ====\n",s);}
int main(){
    ImageParam in(type_of<uint8_t>(),2,"in");
    {   banner("root producer, consumer specialized (branches differ, g emitted once outside)");
        Var x("x"),y("y"),xi("xi"),yi("yi");
        Func g("g"),f("f");
        g(x,y)=in(x,y);
        f(x,y)=g(x,y);
        g.compute_root();
        f.compute_root();
        f.specialize(Param<bool>()).tile(x,y,xi,yi,4,4);
        Func out("out"); out(x,y)=f(x,y); out.print_loop_nest(); }
    {   banner("pointwise g at f.x; specialization SPLITS an outer y (g collapse symmetric)");
        Var x("x"),y("y"),yo("yo"),yi("yi");
        Func g("g"),f("f");
        g(x,y)=in(x,y);
        f(x,y)=g(x,y);
        f.compute_root();
        f.specialize(Param<bool>()).split(y,yo,yi,4);
        g.compute_at(f,x);      // g at inner x in BOTH branches; a point either way
        Func out("out"); out(x,y)=f(x,y); out.print_loop_nest(); }
    return 0;
}
