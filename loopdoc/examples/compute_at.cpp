#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
using namespace Halide;
#endif

// compute_at places the producer's realization *inside* one of the consumer's
// loops. Here the producer is computed afresh for each 'y' scanline of the
// consumer, so its produce/consume block is nested under the consumer's
// 'for y' loop.

int main()
{
    Var x("x"), y("y");

    Func producer("producer");
    producer(x, y) = x + y;

    Func consumer("consumer");
    consumer(x, y) = producer(x, y) + producer(x, y + 1);

    producer.compute_at(consumer, y);

    consumer.print_loop_nest();
}
