#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

template <bool do_clone>
[[nodiscard]] int main_impl(){
    Var x("x");
    ImageParam in(type_of<uint8_t>(),1,"in");
    Func p("p"); p(x)=in(x);
    Func f("f"); f(x)=p(x);
    Func g("g"); g(x)=f(x);
    Func h("h"); h(x)=f(x);
    Func out("out"); out(x)=g(x)+h(x);
    p.compute_at(f, x);
    // The cloned fc will not be happy with the p.compute_at(f, x) that previously
    // was specified, because the p is NOT cloned, and will be realized in the loop
    // nest at a location that fc is not capable of reading from.
    if constexpr (do_clone) {
        Func fc=f.clone_in(g);
        fc.compute_root();
    }
    f.compute_root(); g.compute_root(); h.compute_root();

// Human hypocrisy going on here (violates USE_MICRO_HALIDE rule)
#ifdef USE_MICRO_HALIDE
    out.print_loop_nest();
#else
    try { out.print_loop_nest(); }
    catch (const Halide::CompileError &e){ fprintf(stderr, "CompileError: %s\n", e.what()); return 1; }
#endif

    return 0;
}
