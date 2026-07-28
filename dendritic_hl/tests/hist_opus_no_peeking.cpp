#include "Halide.h"

namespace {

using namespace Halide::ConciseCasts;

class Hist : public Halide::Generator<Hist> {
public:
    Input<Buffer<uint8_t, 3>> input{"input"};
    Output<Buffer<uint8_t, 3>> output{"output"};

    void generate() {
        Var x("x"), y("y"), c("c");

        // Algorithm
        Func Y("Y");
        Y(x, y) = (0.299f * input(x, y, 0) +
                   0.587f * input(x, y, 1) +
                   0.114f * input(x, y, 2));

        Func Cr("Cr");
        Expr R = input(x, y, 0);
        Cr(x, y) = (R - Y(x, y)) * 0.713f + 128;

        Func Cb("Cb");
        Expr B = input(x, y, 2);
        Cb(x, y) = (B - Y(x, y)) * 0.564f + 128;

        Func hist_rows("hist_rows");
        hist_rows(x, y) = 0;
        RDom rx(0, input.width());
        Expr bin = cast<int>(clamp(Y(rx, y), 0, 255));
        hist_rows(bin, y) += 1;

        Func hist("hist");
        hist(x) = 0;
        RDom ry(0, input.height());
        hist(x) += hist_rows(x, ry);

        Func cdf("cdf");
        cdf(x) = hist(0);
        RDom b(1, 255);
        cdf(b.x) = cdf(b.x - 1) + hist(b.x);

        Func cdf_bin("cdf_bin");
        cdf_bin(x, y) = u8(clamp(Y(x, y), 0, 255));

        Func eq("equalize");
        eq(x, y) = clamp(cdf(cdf_bin(x, y)) * (255.0f / (input.height() * input.width())), 0, 255);

        Expr red = u8(clamp(eq(x, y) + (Cr(x, y) - 128) * 1.4f, 0, 255));
        Expr green = u8(clamp(eq(x, y) - 0.343f * (Cb(x, y) - 128) - 0.711f * (Cr(x, y) - 128), 0, 255));
        Expr blue = u8(clamp(eq(x, y) + 1.765f * (Cb(x, y) - 128), 0, 255));
        output(x, y, c) = mux(c, {red, green, blue});

        // Estimates (for autoscheduler; ignored otherwise)
        {
            input.dim(0).set_estimate(0, 1536);
            input.dim(1).set_estimate(0, 2560);
            input.dim(2).set_estimate(0, 3);
            output.dim(0).set_estimate(0, 1536);
            output.dim(1).set_estimate(0, 2560);
            output.dim(2).set_estimate(0, 3);
        }

        // Schedule
        // Idea: fuse output phase into ONE parallel region; kill the 16MB Y and
        // equalize buffers by inlining. Machine is bandwidth-limited.
        const int vec = natural_vector_size<float>();
        Var yo("yo"), yi("yi");

        // Phase 1: histogram. Give the scalar scatter a private per-row Y buffer
        // computed with SIMD (clone; original Y stays inline in the output phase).
        // Block the parallel row loop so the clone buffer allocs once per task.
        Var ho("ho"), hi("hi");
        hist_rows.compute_root();
        hist_rows.update()
            .split(y, ho, hi, 16)
            .parallel(ho);
        Y.clone_in(hist_rows)
            .compute_at(hist_rows, hi)
            .hoist_storage(hist_rows, ho)
            .vectorize(x, vec);

        // Column sum: dense reduction over ry with free bin-axis x. Vectorize
        // across bins (stride-1 in hist_rows), coarse-parallelize the bin groups.
        Var xo("xo"), xi("xi");
        hist.compute_root();
        hist.update()
            .split(x, xo, xi, vec)
            .vectorize(xi)
            .parallel(xo);

        // Serial 256-bin prefix scan (true recurrence).
        cdf.compute_root();

        // Phase 2: single fused pass. Y, Cr, Cb, cdf_bin inline; equalize cached
        // per scanline so its CDF gather runs once/pixel, reused across channels.
        // Wider vector: uint8 store packs better, more float ILP.
        const int ovec = 4 * vec;
        output.reorder(c, x, y)
            .bound(c, 0, 3)
            .unroll(c)
            .split(y, yo, yi, 16)
            .parallel(yo)
            .vectorize(x, ovec);
        eq.compute_at(output, yi)
            .hoist_storage(output, yo)
            .vectorize(x, ovec);
        // Cache Y once per scanline; Cr/Cb/eq all read it instead of recomputing.
        Y.compute_at(output, yi)
            .hoist_storage(output, yo)
            .vectorize(x, ovec);
    }
};

}  // namespace

HALIDE_REGISTER_GENERATOR(Hist, hist)
