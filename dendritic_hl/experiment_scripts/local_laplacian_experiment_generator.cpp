// Experiment generator: local_laplacian with the estimates, the tuned schedule,
// and the HalideTraceViz tags removed, a pre-scheduling `serialize_pipeline`
// checkpoint added for the `new_golden` algorithm-equality check, and a MINIMAL
// compute_root-only schedule below the checkpoint. Also all funcs named.
//
// Derived by hand from apps/local_laplacian/local_laplacian_generator.cpp.  The
// minimal schedule is deliberately un-tuned: it exists only so the pipeline
// compiles and runs in bounded time (a fully-inlined maxJ-deep pyramid would
// never finish), NOT to be fast.  It is less honest than shipping no schedule at
// all, but it guarantees an agent always has a working baseline to improve on.
#include "Halide.h"

#include <string>

namespace {

constexpr int maxJ = 20;

class LocalLaplacian : public Halide::Generator<LocalLaplacian> {
public:
    Input<Buffer<uint16_t, 3>> input{"input"};
    Input<int> levels{"levels"};
    Input<float> alpha{"alpha"};
    Input<float> beta{"beta"};
    Output<Buffer<uint16_t, 3>> output{"output"};

    void generate() {
        /* THE ALGORITHM */
        // Pyramid depth: a fixed constant, not a GeneratorParam (the experiment
        // uses a single fixed algorithm; was `pyramid_levels`, default 8).
        const int J = 8;

        // Make the remapping function as a lookup table.
        Func remap("remap");
        Expr fx = cast<float>(x) / 256.0f;
        remap(x) = alpha * fx * exp(-fx * fx / 2.0f);

        // Set a boundary condition
        Func clamped = Halide::BoundaryConditions::repeat_edge(input);

        // Convert to floating point
        Func floating("floating");
        floating(x, y, c) = clamped(x, y, c) / 65535.0f;

        // Get the luminance channel
        Func gray("gray");
        gray(x, y) = 0.299f * floating(x, y, 0) + 0.587f * floating(x, y, 1) + 0.114f * floating(x, y, 2);

        // Make the processed Gaussian pyramid.
        Func gPyramid[maxJ];
        for (int j = 0; j < J; j++) gPyramid[j] = Func("gPyramid_" + std::to_string(j));
        // Do a lookup into a lut with 256 entries per intensity level
        Expr level = k * (1.0f / (levels - 1));
        Expr idx = gray(x, y) * cast<float>(levels - 1) * 256.0f;
        idx = clamp(cast<int>(idx), 0, (levels - 1) * 256);
        gPyramid[0](x, y, k) = beta * (gray(x, y) - level) + level + remap(idx - 256 * k);
        for (int j = 1; j < J; j++) {
            gPyramid[j](x, y, k) = downsample(gPyramid[j - 1], "gPyramid_" + std::to_string(j) + "_d")(x, y, k);
        }

        // Get its laplacian pyramid
        Func lPyramid[maxJ];
        for (int j = 0; j < J; j++) lPyramid[j] = Func("lPyramid_" + std::to_string(j));
        lPyramid[J - 1](x, y, k) = gPyramid[J - 1](x, y, k);
        for (int j = J - 2; j >= 0; j--) {
            lPyramid[j](x, y, k) = gPyramid[j](x, y, k) - upsample(gPyramid[j + 1], "lPyramid_" + std::to_string(j) + "_u")(x, y, k);
        }

        // Make the Gaussian pyramid of the input
        Func inGPyramid[maxJ];
        for (int j = 0; j < J; j++) inGPyramid[j] = Func("inGPyramid_" + std::to_string(j));
        inGPyramid[0](x, y) = gray(x, y);
        for (int j = 1; j < J; j++) {
            inGPyramid[j](x, y) = downsample(inGPyramid[j - 1], "inGPyramid_" + std::to_string(j) + "_d")(x, y);
        }

        // Make the laplacian pyramid of the output
        Func outLPyramid[maxJ];
        for (int j = 0; j < J; j++) outLPyramid[j] = Func("outLPyramid_" + std::to_string(j));
        for (int j = 0; j < J; j++) {
            // Split input pyramid value into integer and floating parts
            Expr level = inGPyramid[j](x, y) * cast<float>(levels - 1);
            Expr li = clamp(cast<int>(level), 0, levels - 2);
            Expr lf = level - cast<float>(li);
            // Linearly interpolate between the nearest processed pyramid levels
            outLPyramid[j](x, y) = (1.0f - lf) * lPyramid[j](x, y, li) + lf * lPyramid[j](x, y, li + 1);
        }

        // Make the Gaussian pyramid of the output
        Func outGPyramid[maxJ];
        for (int j = 0; j < J; j++) outGPyramid[j] = Func("outGPyramid_" + std::to_string(j));
        outGPyramid[J - 1](x, y) = outLPyramid[J - 1](x, y);
        for (int j = J - 2; j >= 0; j--) {
            outGPyramid[j](x, y) = upsample(outGPyramid[j + 1], "outGPyramid_" + std::to_string(j) + "_u")(x, y) + outLPyramid[j](x, y);
        }

        // Reintroduce color (Connelly: use eps to avoid scaling up noise w/ apollo3.png input)
        Func color("color");
        float eps = 0.01f;
        color(x, y, c) = input(x, y, c) * (outGPyramid[0](x, y) + eps) / (gray(x, y) + eps);

        // Convert back to 16-bit
        output(x, y, c) = cast<uint16_t>(clamp(color(x, y, c), 0.0f, 65535.0f));

        // =====================================================================
        // DO NOT EDIT ANYTHING ABOVE THIS LINE: it is THE ALGORITHM, and the
        // serialize_pipeline() call below snapshots it (pre-scheduling) for the
        // `new_golden` algorithm-equality check.  Editing the algorithm changes
        // the golden fingerprint.  ALL SCHEDULING GOES BELOW THIS LINE.
        // =====================================================================
        if (const char *hlpipe_path = getenv("DENDRITIC_HL_ALGORITHM_HLPIPE")) {
            serialize_pipeline(Pipeline(std::vector<Func>{output}),
                               std::string(hlpipe_path));
        }

        /* THE SCHEDULE (minimal, below the checkpoint).
         * compute_root() at every pyramid level plus remap/gray so nothing is
         * inlined into a pathological, effectively-nonterminating compile.  This
         * is a correctness/liveness floor, NOT a tuned schedule -- improving it
         * is the experiment's whole point. */
        remap.compute_root();
        gray.compute_root();
        for (int j = 0; j < J; j++) {
            inGPyramid[j].compute_root();
            gPyramid[j].compute_root();
            lPyramid[j].compute_root();
            outLPyramid[j].compute_root();
            outGPyramid[j].compute_root();
        }
    }

private:
    Var x, y, c, k;

    // Downsample with a 1 3 3 1 filter.  `name` gives the two separable
    // intermediates readable, unique profiler names (e.g. "gPyramid_1_dx"/"_dy").
    Func downsample(Func f, const std::string &name) {
        // Experiment limitation: don't schedule downy.
        using Halide::_;
        Func downx(name + "x"), downy(name + "y");
        downy(x, y, _) = (f(x, 2 * y - 1, _) + 3.0f * (f(x, 2 * y, _) + f(x, 2 * y + 1, _)) + f(x, 2 * y + 2, _)) / 8.0f;
        downx(x, y, _) = (downy(2 * x - 1, y, _) + 3.0f * (downy(2 * x, y, _) + downy(2 * x + 1, y, _)) + downy(2 * x + 2, y, _)) / 8.0f;
        return downx;
    }

    // Upsample using bilinear interpolation.  `name` names the two separable
    // intermediates (e.g. "outGPyramid_0_ux"/"_uy").
    Func upsample(Func f, const std::string &name) {
        // Experiment limitation: don't schedule upx.
        using Halide::_;
        Func upx(name + "x"), upy(name + "y");
        upx(x, y, _) = lerp(f((x + 1) / 2, y, _), f((x - 1) / 2, y, _), ((x % 2) * 2 + 1) / 4.0f);
        upy(x, y, _) = lerp(upx(x, (y + 1) / 2, _), upx(x, (y - 1) / 2, _), ((y % 2) * 2 + 1) / 4.0f);
        return upy;
    }
};

}  // namespace

HALIDE_REGISTER_GENERATOR(LocalLaplacian, local_laplacian)
