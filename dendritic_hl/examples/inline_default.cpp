#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// The default schedule fully inlines every non-output Func. Here 'producer'
// is given no schedule, so it does not appear in the loop nest at all: its
// definition is substituted into 'consumer'. Only the output Func is realized.

int main()
{
    Var x("x"), y("y");

    Func producer("producer");
    producer(x, y) = x + y;

    Func consumer("consumer");
    consumer(x, y) = producer(x, y) + producer(x + 1, y + 1);

    consumer.print_loop_nest();
}
