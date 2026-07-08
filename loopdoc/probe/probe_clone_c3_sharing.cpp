#include "Halide.h"
using namespace Halide;
#include <stdio.h>
// c3 reads maybe_inlined (which reads common) AND common directly.
// common.clone_in(c1) pins on maybe_inlined (c1->maybe_inlined->common).
// maybe_inlined is ALSO read by c3 => c3's maybe_inlined-path reads of common
// get the clone; c3's DIRECT reads of common stay original.
int main(int argc, char**argv){
    bool dbl = argc>1;
    Var x("x"),y("y"); ImageParam in(type_of<float>(),2,"in");
    Func common("common"); common(x,y)=in(x,y);
    Func maybe_inlined("maybe_inlined"); maybe_inlined(x,y)=common(x,y+1)+common(x+1,y);
    Func c1("c1"); c1(x,y)=maybe_inlined(x,y+1)+maybe_inlined(x+1,y);
    Func c3("c3"); c3(x,y)=maybe_inlined(x,y)+common(x,y)+common(x+1,y+1);
    Func out("out"); out(x,y)=c1(x,y)+c3(x,y);
    Func cc = common.clone_in(c1); cc.compute_root();
    if (dbl) { Func hmm=common.clone_in(c3); hmm.compute_root(); }  // the "crash" line
    common.compute_root(); maybe_inlined.compute_root();
    c1.compute_at(out,y); c3.compute_at(out,y);
    try { out.print_loop_nest(); printf("OK\n"); }
    catch (const Error &e){ fprintf(stderr,"CAUGHT: %s\n", e.what()); return 1; }
    return 0;
}

// FINDING (2026-07-08): common.clone_in(c1) pins the clone on maybe_inlined
// (c1->maybe_inlined->common). c3 also reads maybe_inlined, so c3 sees the clone
// THROUGH that path, while c3's DIRECT reads of common stay original. Printed
// nest shows BOTH `produce common_clone_in_c1` and `produce common` -- common
// survives only because of c3's direct reads. Demonstrates: the wrapper pin is
// on the shared intermediate, so redirection reaches ALL its consumers.
