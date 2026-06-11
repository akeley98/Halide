#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A richer pipeline exercising the storage level alongside compute placement.
// Four stages, each demonstrating a different storage case (section 8):
//
//   a: store_root() + compute_at(b, x)   -> `store a:` is the OUTERMOST node,
//                                           wrapping the whole pipeline body,
//                                           while a is computed deep in b's x loop.
//   b: store_root() + compute_root()     -> store level EQUALS compute level
//                                           (both root) -> NO `store b:` node.
//   c: store_at(output, y)+compute_at(output, x) -> distinct store/compute
//                                           levels -> `store c:` at output's y
//                                           loop, c computed in output's x loop.
//   output: the root output.
//
// Expected loop nest:
//
//   store a:
//     produce b:
//       for y: for x:
//         produce a: for y: for x: a(...) = ...
//         consume a: b(...) = ...
//     consume b:
//       produce output:
//         for y:
//           store c:
//             for x:
//               produce c: for x: c(...) = ...
//               consume c: output(...) = ...

int main()
{
    Var x("x"), y("y");

    ImageParam in(type_of<uint8_t>(), 2, "in");

    Func a("a");
    a(x, y) = in(x, y);

    Func b("b");
    b(x, y) = a(x, y) + a(x + 1, y + 1);

    Func c("c");
    c(x, y) = b(x, y) + b(x, y + 1);

    Func output("output");
    output(x, y) = c(x, y) + c(x + 1, y);

    b.store_root().compute_root();               // store == compute (root): no store node
    a.store_root().compute_at(b, x);             // store_root, computed inside b
    c.store_at(output, y).compute_at(output, x); // distinct store/compute levels

    micro_halide_collapses(c, {y}); // c read at a single y per output pixel -> y loop elides

    output.print_loop_nest();
}
