#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// NEGATIVE example (must fail in both Halide and micro_halide).
//
// Adversarial compute_inline x hoist_storage level (has_hoist_level): a legal
// compute_at(g,y).hoist_storage(g,y) is then overridden by compute_inline(),
// which resets the compute level to inlined but leaves the hoist-storage level
// set. An inlined Func may not carry a hoist level (loopdoc.md section 8), so
// this is illegal -- reached via an inline OVERRIDE, testing that compute_inline
// reset the level so the has_hoist_level/inline check fires. Halide: "Func f is
// scheduled hoist_storage(), but is inlined."

int main()
{
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func f("f"), g("g"), output("output");
    f(x, y) = in(x, y);
    g(x, y) = f(x, y) + f(x + 1, y);
    output(x, y) = g(x, y);

    g.compute_root();
    f.compute_at(g, y).hoist_storage(g, y);  // legal so far
    f.compute_inline();                       // override to inline; hoist level remains -> illegal

    output.print_loop_nest();
}
