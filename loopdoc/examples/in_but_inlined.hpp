#include <stdio.h>
#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif

// Wrapper (`Func::in`) analogue of the clone_in_but_inlined family: it exercises
// the TRANSITIVITY of `in` through an inlined intermediate. `c1` reads `common`
// only through `maybe_inlined`, so `common.in(c1)` pins the wrapper on the direct
// caller `maybe_inlined`, not on `c1` (loopdoc §13 + src_doc/in_clone_in_transitivity.md).
// Because the pin lands on the shared `maybe_inlined`, EVERY consumer of
// `maybe_inlined` reads the wrapper — including `c3`, which was never named.
//
// `c3` additionally reads `common` DIRECTLY, so with the wrapper in place `c3`
// reads the wrapper (via `maybe_inlined`) AND the original `common` (directly);
// both are realized.
//
// Difference from the clone version worth stating: an `in` wrapper *reads* the
// wrapped Func, so `common` is always kept alive by the wrapper and never
// vanishes — unlike a clone, which recomputes and can make `common` unreachable.
// (`in` also has no "can only wrap once" limitation, since it does not deep-copy
// the wrapped Func; see the §13 clone limitation note.)
//
// Params: inlined — leave `maybe_inlined` inline (else compute_root);
//         in_both — wrap for {c1, c2} (else just c1);
//         common_compute_root — common at root (else compute_at(out, y));
//         no_c3 — drop the direct-and-indirect consumer c3.
[[nodiscard]] int in_but_inlined(
    bool inlined,
    bool in_both,
    bool common_compute_root,
    bool no_c3=false)
{
    try {
        Var x("x"), y("y");
        ImageParam in(type_of<float>(), 2, "in");
        Func weird("weird");
        weird(x, y) = in(x, y) * 1337;
        Func common("common");
        common(x, y) = in(x, y + 1) + in(x + 1, y) + weird(x, y) + weird(x + 1, y + 1);
        Func maybe_inlined("maybe_inlined");
        maybe_inlined(x, y) = common(x, y + 1) + common(x + 1, y);
        Func c1("c1");
        c1(x, y) = maybe_inlined(x, y + 1) + maybe_inlined(x + 1, y);
        Func c2("c2");
        c2(x, y) = common(x, y) + in(x + 1, y + 1) + common(x + 1, y + 1);
        Func out("out");
        if (no_c3)
        {
            out(x, y) = c1(x, y) + c1(x+1, y+1) + c2(x, y) + c2(x+1, y+1);
        }
        else
        {
            // c3 reads the wrapper (through maybe_inlined) AND common directly.
            Func c3("c3");
            c3(x, y) = maybe_inlined(x, y) + maybe_inlined(x + 1, y + 1) + common(x, y) + common(x + 1, y + 1);
            out(x, y) = c1(x, y) + c1(x+1, y+1) + c2(x, y) + c2(x+1, y+1) + c3(x, y) + c3(x + 1, y + 1);
            c3.compute_at(out, x);
            // A SECOND wrapper on `common`, pinned on c3 (which calls common
            // directly). This is legal for in(): a Func may carry many `in`
            // wrappers, each redirecting a different consumer path. The clone
            // form of this exact line crashes Halide (clone_in cannot re-wrap an
            // already-wrapped Func, §13) -- the KNOWN, ignored upstream issue
            // https://github.com/halide/Halide/issues/6476 . So c3's DIRECT reads
            // of common go through common_in_c3, while its reads via maybe_inlined
            // go through the other wrapper.
            Func common_in_c3 = common.in(c3);
            common_in_c3.compute_root();
        }

        Func common_wrap = in_both ? common.in({c1, c2}) : common.in(c1);
        common_wrap.compute_root();

        weird.compute_root();
        c1.compute_at(out, y);
        c2.compute_at(out, y);

        if (common_compute_root) {
            common.compute_root();
        }
        else {
            common.compute_at(out, y);
        }

        if (!inlined) {
            maybe_inlined.compute_root();
        }

        out.print_loop_nest();
    }
    catch (const CompileError &e) {
        fprintf(stderr, "CompileError: %s\n", e.what());
        return 1;
    }
#ifndef USE_MICRO_HALIDE
    catch (const InternalError &e) {
        fprintf(stderr, "InternalError: %s\n", e.what());
        return 1;
    }
#endif
    return 0;
}
