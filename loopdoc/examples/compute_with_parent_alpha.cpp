#ifdef USE_MICRO_HALIDE
#include "micro_halide.hpp"
using namespace micro_halide;
#else
#include "Halide.h"
#include "halide_compat.h"
using namespace Halide;
#endif
// "Parent realized last" is an override, not a consequence of the name
// tie-break. Here the parent `aaa` sorts FIRST alphabetically, yet it is still
// the group's last-realized member -> its produce is OUTERMOST. The non-parent
// members keep the §6 tie-break (mmm before zzz) in the body, reversed in the
// produce nesting.
//
//   produce aaa:        # parent outermost despite sorting first
//     produce zzz:
//       produce mmm:
//         for fused.y:
//           for x: aaa  # parent body first
//           for x: mmm
//           for x: zzz
int main() {
    Var x("x"), y("y");
    ImageParam in(type_of<uint8_t>(), 2, "in");
    Func aaa("aaa"), mmm("mmm"), zzz("zzz"), out("out");
    aaa(x, y) = in(x, y);
    mmm(x, y) = in(x, y) + 1;
    zzz(x, y) = in(x, y) + 2;
    out(x, y) = aaa(x, y) + mmm(x, y) + zzz(x, y);
    aaa.compute_root();
    mmm.compute_root();
    zzz.compute_root();
    mmm.compute_with(aaa, y);
    zzz.compute_with(aaa, y);
    out.print_loop_nest();
}
