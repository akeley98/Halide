// Minimal Halide generator used to exercise the two-phase build +
// RunGenMain linking path that dh_hl's build/profile tools need.
#include "Halide.h"

namespace {
using namespace Halide;

class Brighten : public Generator<Brighten> {
public:
    // A GeneratorParam so we can exercise dh_hl's key=value parameter passing.
    GeneratorParam<int> offset{"offset", 10};

    Input<Buffer<uint8_t, 2>>  input{"input"};
    Output<Buffer<uint8_t, 2>> output{"output"};

    Var x{"x"}, y{"y"};

    void generate() {
        output(x, y) = cast<uint8_t>(min(cast<int>(input(x, y)) + offset, 255));
    }

    void schedule() {
        // Estimates let RunGen's --estimate_all synthesize input/output sizes.
        input.set_estimates({{0, 2048}, {0, 2048}});
        output.set_estimates({{0, 2048}, {0, 2048}});
        if (!using_autoscheduler()) {
            output.vectorize(x, 16).parallel(y);
        }
    }
};

}  // namespace

HALIDE_REGISTER_GENERATOR(Brighten, brighten)
