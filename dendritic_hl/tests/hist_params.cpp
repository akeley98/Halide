// A parametrized variant of hist_opus_before_peeking.cpp used by the real-Halide
// harness tests (tests/test_build_cli_halide.py).  Three generator params drive
// the test scenarios:
//
//   * enable_parallel (bool, default true): gates every .parallel() in the
//     schedule.  false -> serial, which is MUCH slower on the estimated image
//     size, so the profiler time visibly moves -- used to check that profiler
//     stats are attributed to the right parameters object.
//   * print_me (string, default ""): if non-empty, printed to stdout at
//     generation time as "GEN_PRINT: <value>".  Used to check the ordering of
//     generator output against the harness's "dh_hl:" generator banners.
//   * should_fail (bool, default false): if true, the generator raises a Halide
//     user error, so the generator subprocess exits nonzero -> a "halide error"
//     build outcome.  Used to check failed-generator handling + that no
//     benchmark set is produced when a subprocess fails.
#include "Halide.h"
#include <iostream>
#include <string>

namespace {

using namespace Halide::ConciseCasts;

class HistParams : public Halide::Generator<HistParams> {
public:
    GeneratorParam<bool> enable_parallel{"enable_parallel", true};
    GeneratorParam<std::string> print_me{"print_me", ""};
    GeneratorParam<bool> should_fail{"should_fail", false};

    Input<Buffer<uint8_t, 3>> input{"input"};
    Output<Buffer<uint8_t, 3>> output{"output"};

    void generate() {
        // Emit the print marker at generation time (before any failure), so the
        // ordering test sees it between the harness's begin/end banners.
        std::string ps = print_me;
        if (!ps.empty()) {
            std::cout << "GEN_PRINT: " << ps << std::endl;
        }
        if (should_fail) {
            // A public Halide exception; GenGen reports it and exits nonzero,
            // which the harness records as a `halide error` build outcome.
            throw Halide::CompileError(
                "intentional generator failure (should_fail=true)");
        }

        Var x("x"), y("y"), c("c");

        // Algorithm (identical to hist_opus_before_peeking.cpp).
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

        input.dim(0).set_estimate(0, 1536);
        input.dim(1).set_estimate(0, 2560);
        input.dim(2).set_estimate(0, 3);
        output.dim(0).set_estimate(0, 1536);
        output.dim(1).set_estimate(0, 2560);
        output.dim(2).set_estimate(0, 3);

        // Schedule.  Every parallel() is gated on enable_parallel; everything
        // else is identical, so the two variants differ ONLY in parallelism.
        const bool par = enable_parallel;
        const int vec = natural_vector_size<float>();
        Var yo("yo"), yi("yi"), ho("ho"), hi("hi"), xo("xo"), xi("xi");

        hist_rows.compute_root();
        hist_rows.update().split(y, ho, hi, 16);
        if (par) hist_rows.update().parallel(ho);
        Y.clone_in(hist_rows)
            .compute_at(hist_rows, hi)
            .hoist_storage(hist_rows, ho)
            .vectorize(x, vec);

        hist.compute_root();
        hist.update().split(x, xo, xi, vec).vectorize(xi);
        if (par) hist.update().parallel(xo);

        cdf.compute_root();

        const int ovec = 4 * vec;
        output.reorder(c, x, y)
            .bound(c, 0, 3)
            .unroll(c)
            .split(y, yo, yi, 16)
            .vectorize(x, ovec);
        if (par) output.parallel(yo);
        eq.compute_at(output, yi)
            .hoist_storage(output, yo)
            .vectorize(x, ovec);
        Y.compute_at(output, yi)
            .hoist_storage(output, yo)
            .vectorize(x, ovec);
    }
};

}  // namespace

HALIDE_REGISTER_GENERATOR(HistParams, histp)
