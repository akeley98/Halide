// A tiny generator for the real-Halide golden tests (tests/test_golden_halide.py).
// It emits the serialized *algorithm* pipeline (pre-scheduling) to the path the
// harness passes in DENDRITIC_HL_ALGORITHM_HLPIPE, exactly as idea.md's
// "New Golden Tool" instructs -- so `new_golden`, the golden check in
// `should_accept`, and `--other golden` can be exercised end to end.
//
// Two generator params drive the scenarios:
//   * add_const (int, default 1): part of the ALGORITHM.  Changing it changes
//     the serialized algorithm hlpipe (a real algorithm deviation).
//   * parallelize (bool, default false): affects only the SCHEDULE, applied
//     AFTER serialization -- so it never changes the algorithm hlpipe.
#include "Halide.h"
#include <cstdlib>
#include <vector>

namespace {

class GoldenGen : public Halide::Generator<GoldenGen> {
public:
    GeneratorParam<int> add_const{"add_const", 1};
    GeneratorParam<bool> parallelize{"parallelize", false};

    Input<Buffer<uint8_t, 2>> input{"input"};
    Output<Buffer<uint8_t, 2>> output{"output"};

    void generate() {
        Var x("x"), y("y");

        // Algorithm: add_const is baked in here, so it is part of the serialized
        // pre-scheduling pipeline below.
        output(x, y) = Halide::cast<uint8_t>(input(x, y) + (int)add_const);

        input.dim(0).set_estimate(0, 256);
        input.dim(1).set_estimate(0, 256);
        output.dim(0).set_estimate(0, 256);
        output.dim(1).set_estimate(0, 256);

        // Output the algorithm as a serialized pipeline, before any scheduling
        // (idea.md "New Golden Tool").
        if (const char *path = getenv("DENDRITIC_HL_ALGORITHM_HLPIPE")) {
            Halide::serialize_pipeline(
                Halide::Pipeline(std::vector<Halide::Func>{output}), path);
        }

        // Schedule only -- does not affect the serialized algorithm above.
        if (parallelize) {
            Var yo("yo"), yi("yi");
            output.split(y, yo, yi, 16).parallel(yo);
        }
    }
};

}  // namespace

HALIDE_REGISTER_GENERATOR(GoldenGen, golden_gen)
