#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
using namespace Halide;
#endif

// The minimal multi-stage example: a producer scheduled compute_root(),
// feeding a consumer (the output, which is always computed at root).
// This exercises the basic "produce/consume" nesting of two pure Funcs.

int main()
{
    Var x("x"), y("y");

    Func producer("producer");
    producer(x, y) = x + y;

    Func consumer("consumer");
    consumer(x, y) = producer(x, y) + producer(x + 1, y + 1);

    producer.compute_root();

    consumer.print_loop_nest();
}
