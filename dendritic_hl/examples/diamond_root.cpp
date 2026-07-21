#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// A "diamond": one producer feeds two intermediate Funcs, which both feed the
// output. With everything compute_root, the shared producer is realized once,
// then each intermediate, then the output -- exercising realization order with
// a Func that has multiple consumers.

int main()
{
    Var x("x"), y("y");

    Func base("base");
    base(x, y) = x + y;

    Func left("left");
    left(x, y) = base(x, y) + 1;

    Func right("right");
    right(x, y) = base(x, y) * 2;

    Func output("output");
    output(x, y) = left(x, y) + right(x, y);

    base.compute_root();
    left.compute_root();
    right.compute_root();

    output.print_loop_nest();
}
