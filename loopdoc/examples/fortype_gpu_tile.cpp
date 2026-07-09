#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `gpu_tile(v, vo, vi, n)` is sugar: it splits `v` by `n` and then types the
// outer (block) loop `gpu_block` and the inner (tile) loop `gpu_thread`, both
// carrying the device (loopdoc.md §17). Expected:
// `gpu_block <outer><Default_GPU>: gpu_thread <inner><Default_GPU>:` -- the same
// shape as fortype_gpu_blocks_threads.cpp but reached through the tile sugar.

int main()
{
    Var x("x"), xo("xo"), xi("xi");
    ImageParam in(type_of<int>(), 1, "in");
    Func f("f");
    f(x) = in(x);
    f.gpu_tile(x, xo, xi, 16);
    f.print_loop_nest();
}
