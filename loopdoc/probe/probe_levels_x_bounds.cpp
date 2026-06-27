// Escalation probe: the genuinely-untested intersection -- a chain whose fuse
// levels are at DIFFERENT depths (so the middle func's shared loops are collapsed
// dummies and the deepest child's loops re-materialize) AND whose members need
// DIFFERENT extents (so prologue/steady/epilogue guarding is in play). If the
// alignment/guarding machinery doesn't account for the re-materialized loop, we
// expect wrong values or OOB. Ground truth = the same pipeline with no
// compute_with (built fresh), so we don't hand-derive.
#include "Halide.h"
#include <cstdio>
#include <string>
using namespace Halide;

// Distinct extents per axis so any extent confusion shows up.
static const int X = 4, Y = 3, Z = 6;

static int fails_total = 0;

// f_level / g_level: 0=z (outer), 1=y, 2=x (inner).  permute_f: read f with x/z
// swapped so f needs a different region than g/h.
static Func build(bool fuse, int f_level, int g_level, bool permute_f, const std::string &tag) {
    Var x("x"), y("y"), z("z");
    Func f("f"), g("g"), h("h"), output("output");
    // values distinct per coordinate to catch index/bounds errors
    f(x, y, z) = x + 10*y + 100*z;
    g(x, y, z) = x + 10*y + 100*z + 1;
    h(x, y, z) = x + 10*y + 100*z + 2;
    Expr fread = permute_f ? f(z, y, x) : f(x, y, z);
    output(x, y, z) = h(x, y, z) + g(x, y, z) + fread;
    f.compute_root(); g.compute_root(); h.compute_root();
    if (fuse) {
        auto V = [&](int k){ return k==0 ? z : (k==1 ? y : x); };
        f.compute_with(g, V(f_level));   // f -> g
        g.compute_with(h, V(g_level));   // g -> h  (chain; spine owner h)
    }
    return output;
}

static void run(int f_level, int g_level, bool permute_f, const std::string &tag) {
    printf("\n==================== %s ====================\n", tag.c_str());
    try {
        Func fused = build(true, f_level, g_level, permute_f, tag);
        fused.compile_to_lowered_stmt("stmt/" + tag + ".stmt.txt", {}, Text);
        Buffer<int> got = fused.realize({X, Y, Z});
        Func ref = build(false, f_level, g_level, permute_f, tag + "_ref");
        Buffer<int> want = ref.realize({X, Y, Z});
        int mism = 0; int fx=-1,fy=-1,fz=-1,gv=0,wv=0;
        for (int zz=0; zz<Z; zz++) for (int yy=0; yy<Y; yy++) for (int xx=0; xx<X; xx++) {
            if (got(xx,yy,zz) != want(xx,yy,zz)) {
                if (mism==0){fx=xx;fy=yy;fz=zz;gv=got(xx,yy,zz);wv=want(xx,yy,zz);}
                mism++;
            }
        }
        if (mism==0) printf("  CORRECT vs reference (%dx%dx%d).\n", X,Y,Z);
        else { fails_total++; printf("  *** WRONG: %d/%d mismatch; first (x=%d,y=%d,z=%d) got %d want %d ***\n",
                                     mism, X*Y*Z, fx,fy,fz,gv,wv); }
    } catch (const Halide::Error &e) {
        // A compile-time rejection is a fine (safe) outcome; a runtime/OOB error is the interesting bad one.
        printf("  ERROR: %s\n", e.what());
    }
}

int main() {
    // f INNER-or-equal to g (well-behaved level-wise), with/without extent skew:
    run(1, 0, false, "Cinner_noperm");   // f@y, g@z
    run(1, 0, true,  "Cinner_perm");     // f@y, g@z, f permuted (extent skew)
    // f OUTER than g (the re-materialization corner), with/without extent skew:
    run(0, 1, false, "Aouter_noperm");   // f@z, g@y
    run(0, 1, true,  "Aouter_perm");     // f@z, g@y, f permuted (extent skew)  <-- prime suspect
    // also equal-level with skew, for completeness:
    run(1, 1, true,  "Bequal_perm");     // f@y, g@y, f permuted
    printf("\n==================== SUMMARY ====================\n  total failing configs: %d\n", fails_total);
    return 0;
}
