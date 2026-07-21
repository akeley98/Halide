#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// GPU directives set both a loop TYPE and a DEVICE, and print_loop_nest shows a
// `<device_api>` suffix on the loop -- the only loop type that carries a device
// (loopdoc.md §17). print_loop_nest shows GPU loops raw (the GPU lowering passes
// do not run on this path). Here split(x) then gpu_blocks(outer)/gpu_threads(inner)
// prints `gpu_block <outer><Default_GPU>: gpu_thread <inner><Default_GPU>:`. The
// harness keeps both the type token and the device suffix.

int main()
{
    Var x("x"), xo("xo"), xi("xi");
    ImageParam in(type_of<int>(), 1, "in");
    Func f("f");
    f(x) = in(x);
    f.split(x, xo, xi, 16);
    f.gpu_blocks(xo);
    f.gpu_threads(xi);
    f.print_loop_nest();
}
