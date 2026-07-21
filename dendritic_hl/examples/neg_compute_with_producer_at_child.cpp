#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// NEGATIVE: the ordinary compute_at rule (enclose EVERY use) applied to a fused
// group. `input` is read by BOTH f and g. Computing it at the CHILD g's slot of
// the shared y loop (input.compute_at(g, y)) does not enclose f's use of input,
// so it is illegal -- the legal sites are input.compute_at(f, ...).
// (Naming the child is NOT categorically illegal: it would be legal if input
// were used only within g -- the child's (g, y) is a real site at g's slot.
// See src_doc/compute_with/member_sites.md.)
int main() {
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<uint8_t>(), 2, "in");
        Func input("input"), f("f"), g("g"), h("h");
        input(x, y) = in(x, y);
        f(x, y) = input(x, y);
        g(x, y) = input(x, y) * 2;
        h(x, y) = f(x, y) + g(x, y);
        f.compute_root();
        g.compute_root();
        g.compute_with(f, y);
        input.compute_at(g, y);   // illegal: g's slot doesn't enclose f's use of input
        h.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());  // Customize printing at your discretion.
        return 1;
    }
    return 0;
}
