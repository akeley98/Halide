#include "Halide.h"
using namespace Halide;
#include <stdio.h>
// Four DISJOINT-consumer scenarios; no paradox/overlap. Does the 2nd call crash?
static int run(const char*label, int mode){
    Var x("x");
    Func f("f"); f(x)=x;
    Func a("a"); a(x)=f(x)+1;   // disjoint consumer
    Func b("b"); b(x)=f(x)+2;   // disjoint consumer
    Func out("out"); out(x)=a(x)+b(x);
    try {
        if (mode==0){ f.clone_in(a); f.clone_in(b); }      // clone then clone
        if (mode==1){ f.in(a);       f.clone_in(b); }      // in then clone
        if (mode==2){ f.clone_in(a); f.in(b); }            // clone then in
        if (mode==3){ f.in(a);       f.in(b); }            // in then in
        f.compute_root();
        out.print_loop_nest();
        printf("%-16s OK\n", label);
    } catch (const CompileError &e){ fprintf(stderr,"%-16s CompileError: %s\n",label,e.what()); }
      catch (const InternalError &e){ fprintf(stderr,"%-16s InternalError: %.60s\n",label,e.what()); }
    return 0;
}
int main(){ run("clone,clone",0); run("in,clone",1); run("clone,in",2); run("in,in",3); return 0; }

// FINDING (2026-07-08): with DISJOINT consumers a,b (no paradox/overlap):
//   clone,clone -> InternalError Schedule.cpp:372   (crash)
//   in,clone    -> InternalError Schedule.cpp:372   (crash)
//   clone,in    -> OK
//   in,in       -> OK
// => The crash is NOT paradox-specific. It fires for ANY second wrapper call
// that is a clone_in on a Func that already carries a wrapper (from in OR
// clone_in), because create_clone_wrapper deep-copies f's schedule (holding the
// prior wrapper) and FuncSchedule::deep_copy can't remap it. in() never deep-
// copies f, so it is unrestricted. clone_in is single-shot per wrapped Func.
