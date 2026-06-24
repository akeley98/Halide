#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// Transitive clone_in(): h reaches f only through g, so f.clone_in(h) redirects
// the DIRECT caller g. The clone reads f's inputs (not f), and f has no other
// consumer, so f DROPS OUT: f_clone_in_h -> g -> h (no produce f). Contrast
// in_transitive, where the wrapper keeps f alive.
int main(){
    Var x("x"); ImageParam in(type_of<uint8_t>(),1,"in");
    Func f("f"); f(x)=in(x);
    Func g("g"); g(x)=f(x);
    Func h("h"); h(x)=g(x);
    Func fc=f.clone_in(h);
    f.compute_root(); fc.compute_root(); g.compute_root(); h.compute_root();
    h.print_loop_nest();
}
