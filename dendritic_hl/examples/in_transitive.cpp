#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// Transitive in(): h reaches f only through g, so f.in(h) redirects the DIRECT
// caller g. The in-wrapper reads f, so f STAYS: f -> f_in_h -> g -> h.
int main(){
    Var x("x"); ImageParam in(type_of<uint8_t>(),1,"in");
    Func f("f"); f(x)=in(x);
    Func g("g"); g(x)=f(x);
    Func h("h"); h(x)=g(x);
    Func fw=f.in(h);
    f.compute_root(); fw.compute_root(); g.compute_root(); h.compute_root();
    h.print_loop_nest();
}
