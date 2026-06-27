// Probe: how broken is Halide compute_with when fused members need DIFFERENT
// bounds? See ../probe_Halide_compute_with_multi_child.md.
//
// Setup: parent(x)=x, child_1(x)=x, child_2(x)=x, all 1-D, all compute_root.
//   output(x,y) = parent(x) + child_1(x) + child_2(y)
// Crucially output reads child_2 at index y, so child_2 is needed over [0,H)
// while parent/child_1 are needed over [0,W). With W != H, fusing along x forces
// guarding/extent-adjustment. Reference (no compute_with): output == 2x + y.
//
// For each case+ordering we (1) compare the FUSED result against 2x+y and (2)
// dump the lowered Stmt to probe/out/*.stmt so we can see where `if` guards land.
#include "Halide.h"
#include <cstdio>
#include <vector>
#include <string>
using namespace Halide;

static const int W = 8;   // x-extent needed by parent, child_1
static const int H = 5;   // y-extent needed by child_2 (W != H on purpose)

static int fails_total = 0;

// schedule selector: which compute_with edges to add.
enum Case { REF, C1_a, C1_b, C2p1, C2p2 };

static Func build(Case c, const std::string &tag, bool more_levels) {
    Var x("x"), y("y");
    Func parent("parent"), child_1("child_1"), child_2("child_2"), output("output");
    if (!more_levels) {
        parent(x) = x;
        child_1(x) = x;
        child_2(x) = x;
        output(x, y) = parent(x) + child_1(x) + child_2(y);
    } else {
        // 2-D members, fuse still at x, to see if an extra loop level matters.
        parent(x, y) = x + y;
        child_1(x, y) = x + y;
        child_2(x, y) = x + y;
        output(x, y) = parent(x, y) + child_1(x, y) + child_2(y, x);
    }
    parent.compute_root();
    child_1.compute_root();
    child_2.compute_root();
    switch (c) {
        case REF: break;
        case C1_a:                                  // both children of parent
            child_1.compute_with(parent, x);
            child_2.compute_with(parent, x);
            break;
        case C1_b:                                  // same, declared other order
            child_2.compute_with(parent, x);
            child_1.compute_with(parent, x);
            break;
        case C2p1:                                  // chain parent<-child_1<-child_2
            child_1.compute_with(parent, x);
            child_2.compute_with(child_1, x);
            break;
        case C2p2:                                  // chain parent<-child_2<-child_1
            child_2.compute_with(parent, x);
            child_1.compute_with(child_2, x);
            break;
    }
    return output;
}

static void run(Case c, const std::string &tag, bool more_levels) {
    printf("\n==================== %s ====================\n", tag.c_str());
    Func output;
    try {
        output = build(c, tag, more_levels);
        // Dump lowered Stmt (shows IfThenElse guards, allocate, etc.).
        std::string fn = "stmt/" + tag + ".stmt.txt";
        output.compile_to_lowered_stmt(fn, {}, Text);
        printf("  wrote %s\n", fn.c_str());
    } catch (const CompileError &e) {
        printf("  COMPILE ERROR: %s\n", e.what());
        return;
    }
    // Correctness vs reference 2x+y (1-D members) / parent+child_1+child_2 (2-D).
    try {
        Buffer<int> out = output.realize({W, H});
        int mism = 0, first_x = -1, first_y = -1, got = 0, want = 0;
        for (int yy = 0; yy < H; yy++) {
            for (int xx = 0; xx < W; xx++) {
                int expect = more_levels ? ( (xx+yy) + (xx+yy) + (yy+xx) )
                                         : ( 2*xx + yy );
                int v = out(xx, yy);
                if (v != expect) {
                    if (mism == 0) { first_x = xx; first_y = yy; got = v; want = expect; }
                    mism++;
                }
            }
        }
        if (mism == 0) {
            printf("  CORRECT: all %dx%d outputs match reference.\n", W, H);
        } else {
            fails_total++;
            printf("  *** WRONG: %d/%d mismatch; first at (x=%d,y=%d) got %d want %d ***\n",
                   mism, W*H, first_x, first_y, got, want);
        }
    } catch (const Halide::RuntimeError &e) {
        fails_total++;
        printf("  *** RUNTIME ERROR (e.g. OOB): %s ***\n", e.what());
    } catch (const Halide::Error &e) {
        fails_total++;
        printf("  *** ERROR: %s ***\n", e.what());
    }
}

int main() {
    for (bool more : {false, true}) {
        const char *suffix = more ? "_2d" : "_1d";
        run(REF,  std::string("ref")    + suffix, more);
        run(C1_a, std::string("case1a") + suffix, more);
        run(C1_b, std::string("case1b") + suffix, more);
        run(C2p1, std::string("case2p1")+ suffix, more);
        run(C2p2, std::string("case2p2")+ suffix, more);
    }
    printf("\n==================== SUMMARY ====================\n");
    printf("  total failing configs: %d\n", fails_total);
    return 0;
}
