#include "Halide.h"
using namespace Halide;
#include <stdio.h>
int main(){
    Var x("x"),y("y"); ImageParam in(type_of<float>(),2,"in");
    Func common("common"); common(x,y)=in(x,y);
    Func maybe_inlined("maybe_inlined"); maybe_inlined(x,y)=common(x,y+1)+common(x+1,y);
    Func c1("c1"); c1(x,y)=maybe_inlined(x,y+1)+maybe_inlined(x+1,y);
    Func c3("c3"); c3(x,y)=maybe_inlined(x,y)+common(x,y)+common(x+1,y+1);
    Func out("out"); out(x,y)=c1(x,y)+c3(x,y);
    try {
        Func cc = common.clone_in(c1); cc.compute_root();
        Func hmm = common.clone_in(c3); hmm.compute_root();
        common.compute_root(); maybe_inlined.compute_root();
        out.print_loop_nest();
    } catch (const CompileError &e){ fprintf(stderr,"CompileError: %s\n", e.what()); return 1; }
      catch (const InternalError &e){ fprintf(stderr,"InternalError: %s\n", e.what()); return 1; }
    return 0;
}

// FINDING (2026-07-08): common.clone_in(c1) then common.clone_in(c3) throws an
// InternalError at CALL time (not lowering):
//   Internal error at src/Schedule.cpp:372  Condition failed: copied_func.defined()
//   common_clone_in_c1$0$0
// create_clone_wrapper deep-copies common's FunctionContents (seeding copied_map
// with only common's self-remap); FuncSchedule::deep_copy then tries to carry
// over common's EXISTING wrappers map (common_clone_in_c1) through copied_map,
// which was never populated with it -> assert. => cloning an already-wrapped
// Func is unsupported by the single-Func deep-copy path. Halide limitation.
