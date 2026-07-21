#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
int main(){
    try {
        Var x("x"), y("y"); ImageParam in(type_of<uint8_t>(),2,"in");
        Func p("p"); p(x,y)=in(x,y);
        Func f("f"); f(x,y)=p(x,y);
        Func g("g"); g(x,y)=f(x,y);
        Func h("h"); h(x,y)=f(x,y);
        Func out("out"); out(x,y)=g(x,y)+h(x,y);
        Func fc=f.clone_in(g);
        Func outc = out.clone_in(g);  // g doesn't actually consume out
        p.compute_root(); f.compute_root(); fc.compute_root();
        g.compute_root(); h.compute_root();
        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
    return 0;
}
