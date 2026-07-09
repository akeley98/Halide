#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// `gpu_single_thread()` wraps the whole stage in a single (extent-1) GPU block +
// thread loop pair, outside the existing loops (loopdoc.md §17). So f(x) prints
// three nested loops: `gpu_block <...><Default_GPU>:`, `gpu_thread <...><Default_GPU>:`,
// then the original `for x:`, then the leaf.
//
// This is a STRUCTURE test of the directive, not a test of the extent-1 GPU
// survival rule (§17): micro renders every non-collapsed dimension, and real
// Halide keeps the two extent-1 GPU loops because their device is non-None -- so
// both show the block+thread+serial nest, coinciding without micro reasoning
// about extents. The block/thread loops carry the `<Default_GPU>` device suffix,
// which the harness keeps.

int main()
{
    Var x("x");
    ImageParam in(type_of<int>(), 1, "in");
    Func f("f");
    f(x) = in(x);
    f.gpu_single_thread();
    f.print_loop_nest();
}
