#include "Halide.h"
#include <cstdio>
using namespace Halide;
static void banner(const char *s){fprintf(stderr,"\n==== %s ====\n",s);}
int main(){
    ImageParam in(type_of<uint8_t>(),2,"in");
    {   banner("basic 8arg tile");
        Var x("x"),y("y"),xo("xo"),yo("yo"),xi("xi"),yi("yi");
        Func f("f"),out("out"); f(x,y)=in(x,y); out(x,y)=f(x,y);
        f.compute_root(); Param<bool> c;
        f.specialize(c).tile(x,y,xo,yo,xi,yi,4,4);
        out.print_loop_nest(); }
    {   banner("inherit 8arg tile + split xi");
        Var x("x"),y("y"),xo("xo"),yo("yo"),xi("xi"),yi("yi"),xii("xii");
        Func f("f"),out("out"); f(x,y)=in(x,y); out(x,y)=f(x,y);
        f.compute_root().tile(x,y,xo,yo,xi,yi,4,4); Param<bool> c;
        f.specialize(c).split(xi,xi,xii,2);
        out.print_loop_nest(); }
    {   banner("nested 8arg");
        Var x("x"),y("y"),xo("xo"),yo("yo"),xi("xi"),yi("yi"),xii("xii");
        Func f("f"),out("out"); f(x,y)=in(x,y); out(x,y)=f(x,y);
        f.compute_root(); Param<bool> c1,c2;
        f.specialize(c1).tile(x,y,xo,yo,xi,yi,4,4).specialize(c2).split(xi,xi,xii,2);
        out.print_loop_nest(); }
    return 0;
}
