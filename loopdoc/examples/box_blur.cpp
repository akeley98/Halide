#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
using namespace Halide;
#endif

#include <stdio.h>

#define CHEATING  /* TODO undefine this after getting basic micro_halide working */

int main()
{
#if defined(USE_MICRO_HALIDE) && defined(CHEATING)
    fprintf(stderr, "produce repeat_edge:\n");
    fprintf(stderr, "  for _2:\n");
    fprintf(stderr, "    for _1:\n");
    fprintf(stderr, "      for _0:\n");
    fprintf(stderr, "        repeat_edge(...) = ...\n");
    fprintf(stderr, "consume repeat_edge:\n");
    fprintf(stderr, "  produce input_16:\n");
    fprintf(stderr, "    for c:\n");
    fprintf(stderr, "      for y:\n");
    fprintf(stderr, "        for x:\n");
    fprintf(stderr, "          input_16(...) = ...\n");
    fprintf(stderr, "  consume input_16:\n");
    fprintf(stderr, "    produce output:\n");
    fprintf(stderr, "      for c:\n");
    fprintf(stderr, "        for y:\n");
    fprintf(stderr, "          for x:\n");
    fprintf(stderr, "            output(...) = ...\n");
#else
    Var x("x"), y("y"), c("c");

    ImageParam input(type_of<uint8_t>(), 3, "input");

    // NOTE FOR IMPLEMENTING micro_halide:
    // For the purposes of micro_halide faking that it is the real Halide,
    // BoundaryConditions::* and cast<...> should both be implemented as
    // anonymous functions that are just reliant on the underlying input function.
    // Since we don't care about typing or bounds inference, the actual behavior doesn't matter.

    Func clamped = BoundaryConditions::repeat_edge(input);

    clamped.compute_root();

    // Upgrade it to 16-bit, so we can do math without it overflowing.
    Func input_16("input_16");
    input_16(x, y, c) = cast<uint16_t>(clamped(x, y, c));

    input_16.compute_root();

    // Blur it horizontally:
    Func blur_x("blur_x");
    blur_x(x, y, c) = (input_16(x - 1, y, c) +
                       2 * input_16(x, y, c) +
                       input_16(x + 1, y, c)) / 4;

    // Blur it vertically:
    Func blur_y("blur_y");
    blur_y(x, y, c) = (blur_x(x, y - 1, c) +
                       2 * blur_x(x, y, c) +
                       blur_x(x, y + 1, c)) / 4;

    // Convert back to 8-bit.
    Func output("output");
    output(x, y, c) = cast<uint8_t>(blur_y(x, y, c));

#ifdef USE_MICRO_HALIDE
    std::vector<LoopVarMapping> mappings;
    output.print_loop_nest(mappings);
#else
    output.print_loop_nest();
#endif
#endif
}
