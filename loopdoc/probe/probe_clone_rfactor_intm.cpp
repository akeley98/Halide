#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
#include <stdio.h>
// Mirror B3 minus specialize: rfactor h FIRST, then clone_in({g, hintm}).
// p kept alive ONLY by inline keep. Expect f before p.
int main(){
    Var x("x"),y("y"),u("u"); ImageParam in(type_of<int>(),2,"in");
    RDom r(0,4,0,4,"r");
    Func p("p"); p(x,y)=cast<int>(in(x,y));
    Func g("g"); g(x,y)=p(x,y)+1;
    Func h("h"); h(x,y)=0; h(x,y)+=p(x+r.x,y+r.y);
    Func hintm=h.update(0).rfactor(r.y,u);
    Func pc=p.clone_in({g,hintm});
    Func keep("keep"); keep(x,y)=p(x,y)*2;
    Func f("f"); f(x,y)=g(x,y)+h(x,y);
    Func out("out"); out(x,y)=f(x,y)+keep(x,y);
    p.compute_root(); f.compute_root();
    g.compute_at(f,y); h.compute_at(f,y); hintm.compute_at(f,y); pc.compute_at(f,y);
    out.print_loop_nest();
    return 0;
}

// FINDING (probe run 2026-07-08):
//   REAL Halide root-level order: produce f ... consume f: produce p ...
//   MICRO root-level order:       produce p ... consume p: produce f ...
// The clone p_clone is realized once inside f and feeds BOTH g and h_intm; the
// original p is read ONLY by inline keep, so real Halide realizes p after f.
// Micro drags p to root => micro leaves h_intm reading the ORIGINAL p, i.e. the
// clone_in({g, hintm}) redirection did not reach the rfactor intermediate hintm.
// Companion probes that ISOLATE the trigger (all order p correctly in micro):
//   - plain DFS depth gating: examples/realization_order_dfs.cpp (GREEN)
//   - plain clone, consumer compute_root: correct
//   - clone consumer compute_at(f): correct
// Only adding the rfactor-intermediate-as-clone-consumer flips p to root.
// => B3 root cause is clone/in redirection, NOT the realization-order rule.
