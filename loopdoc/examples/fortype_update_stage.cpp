#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Loop types are PER STAGE, like the §9 transforms (loopdoc.md §17). Here f has
// a pure stage and one update stage; `f.update(0).parallel(x)` types only the
// update stage's loop. Both stages live in one `produce f`, in stage order, so
// the observable is `for <x>:` (pure) followed by `parallel <x>:` (update) --
// the parallel token appears on the second nest only.

int main()
{
    Var x("x");
    ImageParam in(type_of<int>(), 1, "in");
    Func f("f");
    f(x) = in(x);           // pure stage: serial
    f(x) = f(x) + 1;        // update stage 0
    f.update(0).parallel(x);
    f.print_loop_nest();
}
