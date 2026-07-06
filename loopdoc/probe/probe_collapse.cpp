#include "Halide.h"
#include <cstdio>
using namespace Halide;
static void banner(const char *s){fprintf(stderr,"\n==== %s ====\n",s);}
int main() {
    ImageParam in(type_of<uint8_t>(), 2, "in");
    {   banner("bare specialize, no schedule change (expect COLLAPSE to 1)");
        Var x("x"), y("y"); Func f("f");
        f(x,y)=in(x,y); Param<bool> c;
        f.compute_root(); f.specialize(c);
        Func out("out"); out(x,y)=f(x,y); out.print_loop_nest(); }
    {   banner("specialize .reorder only (canonicalize-invisible; expect 2 printed branches)");
        Var x("x"), y("y"); Func f("f");
        f(x,y)=in(x,y); Param<bool> c;
        f.compute_root(); f.specialize(c).reorder(y,x);
        Func out("out"); out(x,y)=f(x,y); out.print_loop_nest(); }
    return 0;
}
