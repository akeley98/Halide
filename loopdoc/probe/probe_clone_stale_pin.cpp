#include "Halide.h"
using namespace Halide;
#include <stdio.h>
int main(){
    Var x("x"),y("y"),u("u"); ImageParam in(type_of<int>(),2,"in");
    RDom r(0,4,0,4,"r");
    Func p("p"); p(x,y)=cast<int>(in(x,y));
    Func g("g"); g(x,y)=p(x,y)+1;
    Func h("h"); h(x,y)=0; h(x,y)+=p(x+r.x,y+r.y);
    Func pc=p.clone_in({g,h});          // pin wrapper on g and h (both call p now)
    Func hintm=h.update(0).rfactor(r.y,u);  // severs h's direct call to p
    Func f("f"); f(x,y)=g(x,y)+h(x,y);
    Func out("out"); out(x,y)=f(x,y);
    p.compute_root(); pc.compute_root(); f.compute_root();
    try { out.print_loop_nest(); }
    catch (const Error &e) { fprintf(stderr, "CAUGHT: %s\n", e.what()); return 1; }
    return 0;
}

// FINDING (2026-07-08): confirms the in/clone_in legality mechanism.
//   Output: CAUGHT: Error: Cannot wrap "p" in "h" because "h" does not call "p"
//           Direct callees of "h" are:  h_intm
// clone_in({g,h}) PINS the wrapper on h (h's update calls p at that moment).
// rfactor then moves h's read of p into h_intm, so at LOWERING h calls only
// h_intm -> the pin on h is STALE -> WrapCalls.cpp validate_custom_wrapper errors.
// NOT a claim h can't reach p (it reaches it via h_intm). Contrast the GREEN
// examples/clone_in_but_inlined.hpp where common.clone_in(c1) pins on the direct
// caller maybe_inlined (c1->maybe_inlined->common) and is legal.
// Source: src/Func.cpp resolve_transitive_callers/get_wrapper, src/WrapCalls.cpp.
