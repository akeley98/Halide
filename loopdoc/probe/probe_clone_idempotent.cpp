#include "Halide.h"
using namespace Halide;
#include <stdio.h>
static void run(const char*label,int mode){
    Var x("x");
    Func f("f"); f(x)=x;
    Func a("a"); a(x)=f(x)+1;
    Func b("b"); b(x)=f(x)+2;
    Func out("out"); out(x)=a(x)+b(x);
    try {
        if(mode==0){ f.clone_in(a); f.clone_in(a); }        // SAME consumer twice
        if(mode==1){ f.clone_in(a); f.clone_in(b); }        // NEW consumer 2nd time
        if(mode==2){ Func c=f.clone_in(a); c.clone_in(b);}  // clone-of-CLONE (agent's line 256 shape)
        f.compute_root(); out.print_loop_nest(); printf("%-14s OK\n",label);
    } catch(const CompileError&e){fprintf(stderr,"%-14s CompileError\n",label);}
      catch(const InternalError&e){fprintf(stderr,"%-14s InternalError %.40s\n",label,e.what());}
}
int main(){ run("same-consumer",0); run("new-consumer",1); run("clone-of-clone",2); }

// FINDING (2026-07-08): the double-clone crash is gated on create_clone_wrapper
// actually running. get_wrapper returns a CACHED wrapper if the consumer key is
// already registered, skipping the deep copy.
//   same-consumer  (f.clone_in(a); f.clone_in(a))  -> OK  (idempotent, cached)
//   new-consumer   (f.clone_in(a); f.clone_in(b))  -> InternalError (crash)
//   clone-of-clone (c=f.clone_in(a); c.clone_in(b))-> CompileError, NOT a crash
//                   (b doesn't call the clone c; and c's wrappers map is empty)
// So a Func may carry at most ONE distinct clone/in wrapper before a later
// clone_in that needs a NEW key crashes. Matches Halide's own idempotency test
// test/correctness/func_clone.cpp:43-44 (reordered-but-equal consumer lists).
