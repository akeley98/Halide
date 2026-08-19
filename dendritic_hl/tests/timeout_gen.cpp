// Generator fixture for the build-timeout tests (tests/test_build_timeout_halide.py).
//
// It exercises the two -- and only two -- build steps dh_hl can time-limit:
//
//   * The GENERATOR EMIT (dh_hl build --gen-timeout).  If the env var
//     DH_HL_TEST_HANG_MS is a positive integer, generate() busy-waits that many
//     milliseconds instead of building a pipeline, simulating a schedule that
//     blows up during lowering.  While it waits it appends the elapsed seconds to
//     the file named by DH_HL_TEST_HEARTBEAT (flushed each tick), so the test can
//     read back exactly how long the process lived before it was killed -- which
//     isolates the SIGTERM-vs-SIGKILL timing from the (variable) C++ compile time.
//     If DH_HL_TEST_IGNORE_SIGTERM is set, it installs SIG_IGN for SIGTERM first,
//     so only the SIGKILL backstop can stop it (covers the escalation path).
//     GenGen only calls generate() for a real emit (-g ...), never for generator
//     discovery (no -g) or runtime emit (-r ...), so those steps never hang.
//
//   * The PIPELINE RUN (dh_hl build --exec-timeout).  The `runtime_work` generator
//     param, when > 0, makes the output each element sum a transcendental chain
//     over an RDom of that many iterations, scheduled serially.  The IR stays tiny
//     (fast C++ compile) but a single run takes far longer than any sane
//     --exec-timeout, so the run is killed mid-flight.  With runtime_work == 0 the
//     pipeline is a trivial copy (used by the emit-hang tests, which never run it).
#include "Halide.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <string>
#include <unistd.h>

namespace {

class TimeoutGen : public Halide::Generator<TimeoutGen> {
public:
    GeneratorParam<int> runtime_work{"runtime_work", 0};

    Input<Buffer<uint8_t, 2>> input{"input"};
    Output<Buffer<uint8_t, 2>> output{"output"};

    void generate() {
        maybe_hang();

        Halide::Var x("x"), y("y");
        const int work = runtime_work;
        if (work > 0) {
            // Expensive-to-run, cheap-to-compile: a transcendental reduction per
            // output element, run serially so a single realization is slow.
            Halide::Func acc("acc");
            acc(x, y) = 0.0f;
            Halide::RDom r(0, work);
            acc(x, y) += Halide::sin(Halide::cast<float>(r + input(x, y))) *
                         Halide::cos(Halide::cast<float>(r));
            output(x, y) =
                Halide::cast<uint8_t>(Halide::clamp(acc(x, y), 0.0f, 255.0f));
            acc.compute_root();  // deliberately no parallel/vectorize: slow
        } else {
            output(x, y) = input(x, y);
        }
        input.dim(0).set_estimate(0, 128);
        input.dim(1).set_estimate(0, 128);
        output.dim(0).set_estimate(0, 128);
        output.dim(1).set_estimate(0, 128);
    }

private:
    void maybe_hang() {
        const char *hang_ms_env = std::getenv("DH_HL_TEST_HANG_MS");
        long hang_ms = hang_ms_env ? std::atol(hang_ms_env) : 0;
        if (hang_ms <= 0) {
            return;
        }
        if (std::getenv("DH_HL_TEST_IGNORE_SIGTERM")) {
            std::signal(SIGTERM, SIG_IGN);
        }
        const char *hb = std::getenv("DH_HL_TEST_HEARTBEAT");
        std::FILE *f = hb ? std::fopen(hb, "w") : nullptr;
        auto start = std::chrono::steady_clock::now();
        for (;;) {
            double elapsed = std::chrono::duration<double>(
                                 std::chrono::steady_clock::now() - start)
                                 .count();
            if (f) {
                std::fprintf(f, "%f\n", elapsed);
                std::fflush(f);
            }
            if (elapsed * 1000.0 >= (double)hang_ms) {
                break;  // not killed in time: fall through so the build "succeeds"
            }           // and the test fails loudly rather than hanging forever.
            usleep(20 * 1000);
        }
        if (f) {
            std::fclose(f);
        }
    }
};

}  // namespace

HALIDE_REGISTER_GENERATOR(TimeoutGen, timeoutg)
