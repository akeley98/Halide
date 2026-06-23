#pragma once

// Top-level micro_halide header.
//
// "Drop-in" tiny replacement for a subset of Halide, sufficient only
// to define simple pure functions, schedule them with compute_root /
// compute_at (or leave them inlined), and print their loop nest.
//
// micro_halide may skip implementing things only needed for convenience,
// like auto-uniquely-named variables. It does NOT generate executable code.
//
// The loop nests are compared *structurally* (see ../canonicalize.py):
// loop variable names and constant bounds are normalized away, so
// micro_halide does not need to reproduce them. It only needs to match the
// produce/consume/store nesting, loop ordering, and loop type.

#include <algorithm>
#include <cctype>
#include <initializer_list>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace micro_halide
{

// ---------------------------------------------------------------------------
// Var: a pure loop variable / dimension name.
// ---------------------------------------------------------------------------
class Var
{
    std::string _name;

  public:
    // With micro_halide, always name variables explicitly.
    Var(std::string n) : _name(std::move(n))
    {
    }

    const std::string &name() const
    {
        return _name;
    }
};

// ---------------------------------------------------------------------------
// Type: meaningless placeholder kept only for API compatibility. micro_halide
// does no type checking or bounds inference.
// ---------------------------------------------------------------------------
struct Type
{
};

template <typename T>
Type type_of()
{
    return Type{};
}

// Forward declarations.
struct FuncContents;
class FuncRef;

// ---------------------------------------------------------------------------
// Expr: a value expression. For loop-nest purposes we do NOT track the actual
// arithmetic; we only track which Funcs the expression reads from (its
// "producers"). That dependency set is all that is needed to deduce the
// producer/consumer graph and therefore the loop nest.
// ---------------------------------------------------------------------------
class RVar; // reduction variable (defined below, after Expr)

class Expr
{
  public:
    // Funcs this expression reads from (may contain duplicates; deduped later).
    std::vector<std::shared_ptr<FuncContents>> deps;
    // Names of reduction variables (RVars) referenced anywhere in this
    // expression (may contain duplicates; deduped later). Captured so that an
    // update definition can tell which reduction loops it needs. RVar names are
    // distinct per RVar (e.g. "r", or "r$x"/"r$y" for a multi-dim RDom); the
    // harness drops loop names, so only the COUNT/identity matters.
    std::vector<std::string> rvars;

    Expr()
    {
    }
    Expr(int)
    {
    }
    Expr(float)
    {
    }
    Expr(double)
    {
    }
    Expr(const Var &)
    {
    }
    // An RVar used as an expression carries its name (see definition below).
    Expr(const RVar &);
    // Reading a Func (a FuncRef) inside an expression adds it as a producer.
    Expr(const FuncRef &);
};

inline Expr combine(const Expr &a, const Expr &b)
{
    Expr r;
    r.deps = a.deps;
    r.deps.insert(r.deps.end(), b.deps.begin(), b.deps.end());
    r.rvars = a.rvars;
    r.rvars.insert(r.rvars.end(), b.rvars.begin(), b.rvars.end());
    return r;
}

inline Expr operator+(const Expr &a, const Expr &b)
{
    return combine(a, b);
}
inline Expr operator-(const Expr &a, const Expr &b)
{
    return combine(a, b);
}
inline Expr operator*(const Expr &a, const Expr &b)
{
    return combine(a, b);
}
inline Expr operator/(const Expr &a, const Expr &b)
{
    return combine(a, b);
}

// cast<T>(e): typing is irrelevant to the loop nest, so this is a pass-through
// that preserves e's producer dependencies.
template <typename T>
Expr cast(const Expr &e)
{
    return e;
}

// clamp(e, lo, hi): bounds an index/value. Typing and bounds are irrelevant to
// the loop nest, so this is a pass-through preserving e's deps and rvars (lo/hi
// are plain Exprs and contribute their own, if any).
inline Expr clamp(const Expr &e, const Expr &lo, const Expr &hi)
{
    return combine(e, combine(lo, hi));
}

// ---------------------------------------------------------------------------
// RVar: one dimension of a reduction domain. Like a Var it just names a loop,
// but it only appears in update definitions and yields reduction loops there.
// ---------------------------------------------------------------------------
class RVar
{
    std::string _name;

  public:
    explicit RVar(std::string n) : _name(std::move(n))
    {
    }
    const std::string &name() const
    {
        return _name;
    }
};

inline Expr::Expr(const RVar &r)
{
    rvars.push_back(r.name());
}

// ---------------------------------------------------------------------------
// RDom: a reduction domain. Declares one or more RVars. Bounds are irrelevant
// to the loop structure (the harness drops constant bounds), so only the
// RVars' identities matter. A 1-D RDom is usable directly as its single RVar
// (and hence as an Expr / a scheduling handle).
// ---------------------------------------------------------------------------
class RDom
{
  public:
    RVar x, y, z, w;
    int dims;

    // 1-D: RDom r(min, extent, name);
    RDom(int /*min*/, int /*extent*/, std::string name)
        : x(name), y(name + "$y"), z(name + "$z"), w(name + "$w"), dims(1),
          _name(std::move(name))
    {
    }
    // 2-D: RDom r(minx, extentx, miny, extenty, name); RVars are name$x, name$y.
    RDom(int, int, int, int, std::string name)
        : x(name + "$x"), y(name + "$y"), z(name + "$z"), w(name + "$w"), dims(2),
          _name(std::move(name))
    {
    }
    // 3-D.
    RDom(int, int, int, int, int, int, std::string name)
        : x(name + "$x"), y(name + "$y"), z(name + "$z"), w(name + "$w"), dims(3),
          _name(std::move(name))
    {
    }

    // A 1-D RDom is usable as its single RVar.
    const std::string &name() const
    {
        return dims == 1 ? x.name() : _name;
    }
    operator Expr() const
    {
        return Expr(x);
    }

  private:
    std::string _name;
};

struct DimData
{
    std::string _name;

    DimData(std::string n) : _name(std::move(n))
    {
    }

    const std::string &name() const
    {
        return _name;
    }

    template <typename VarList>
    static std::vector<DimData> from_var_list(const VarList& lst)
    {
        std::vector<DimData> result;
        result.reserve(lst.size());
        for (const auto& var : lst) {
            result.emplace_back(var.name());
        }
        return result;
    }
};

// ---------------------------------------------------------------------------
// FuncContents: the shared, mutable state behind a Func handle (mirrors
// Halide::Internal::Function). Copying a Func shares this state.
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// StageData: the per-STAGE state (mirrors Halide's Definition + StageSchedule).
// Stage 0 is the pure / initial definition; stages 1.. are update definitions
// (loopdoc.md section 10). Every stage -- pure or update -- has the SAME shape,
// so the printer treats them uniformly.
// ---------------------------------------------------------------------------
struct StageData
{
    // This stage's ordered loop dimensions, innermost first (dims[0] is the
    // innermost loop). split/fuse/reorder/tile rewrite this list; for an update
    // stage it also contains the RVar loops.
    std::vector<DimData> dims;

    // Loop variables of THIS stage that Halide elides (point loops, extent 1).
    // *Declared* per example via micro_halide_collapses (it needs bounds
    // inference, which is out of scope); an elided loop drops its `for` line but
    // is still a valid injection site. Each stage has its own set, matching the
    // real-Halide API where micro_halide_collapses(f) targets the pure stage and
    // micro_halide_collapses(f.update(N)) targets update stage N.
    std::set<std::string> collapsed;

    // Funcs read by THIS stage (deduped). Per-stage so the section-10 legal-site
    // rule can tell which specific stage of a reader uses a producer.
    std::vector<std::shared_ptr<FuncContents>> producers;

    // Names of THIS stage's dimensions that are RVar (reduction) loops, as
    // opposed to free Vars. Needed because a 1-D RDom's RVar name has no "$"
    // suffix and so is indistinguishable from a Var by name alone -- rfactor
    // (loopdoc.md section 12) must know which dims are RVars to decide which to
    // drop in the merge stage. The pure stage has none.
    std::set<std::string> rvars;
};

// ---------------------------------------------------------------------------
// FuncContents: the WHOLE-FUNC state behind a Func handle (mirrors
// Halide::Internal::Function + FuncSchedule). Copying a Func shares this state.
// Per-stage state lives in `stages` (StageData); the fields here apply to the
// Func as a whole -- in particular the compute / store / hoist levels move ALL
// stages together.
// ---------------------------------------------------------------------------
struct FuncContents
{
    std::string name;
    bool defined = false;

    // The stages: stages[0] is the pure/initial definition, stages[k>0] the
    // k-th update definition (so Func::update(i) refers to stages[i+1]).
    std::vector<StageData> stages;

    // Funcs read by this Func across ALL stages, deduplicated (used for
    // realization order). Per-stage reads live in each StageData::producers.
    std::vector<std::shared_ptr<FuncContents>> producers;

    // Where this Func is computed in the loop nest (whole-Func: all stages).
    enum class Level
    {
        Inline, // default: substituted into its consumers, no loops of its own
        Root,   // computed once at the outermost level
        At      // computed inside at_func's loop over at_var
    };
    Level level = Level::Inline;

    std::shared_ptr<FuncContents> at_func; // host, for Level::At
    std::string at_var;                    // host loop var name, for Level::At

    // Store level (where this Func's buffer is allocated; whole-Func), set by
    // store_at / store_root. Defaults to "same as the compute level"
    // (has_store_level == false), which prints no `store` node.
    bool has_store_level = false;          // a store level was explicitly set
    bool store_is_root = false;            // store_root() (else store_at)
    std::shared_ptr<FuncContents> store_func; // host, for store_at
    std::string store_var;                 // host loop var name, for store_at

    // Hoist-storage level (where the physical allocation is placed; whole-Func),
    // set by hoist_storage / hoist_storage_root. Has NO effect on
    // print_loop_nest output (loopdoc.md section 8); only legality.
    bool has_hoist_level = false;          // a hoist-storage level was set
    bool hoist_is_root = false;            // hoist_storage_root() (else hoist_storage)
    std::shared_ptr<FuncContents> hoist_func; // host, for hoist_storage
    std::string hoist_var;                 // host loop var name, for hoist_storage
};

// ---------------------------------------------------------------------------
// Loop-transform primitives (loopdoc.md section 6), operating directly on an
// ordered dimension list (args[0] = INNERMOST). Both a Func's pure stage
// (a Func's pure stage and an update Stage, both StageData::dims) share this.
// ---------------------------------------------------------------------------
namespace dimlist
{
inline int dim_pos(const std::vector<DimData> &a, const std::string &name)
{
    for (int i = 0; i < (int)a.size(); i++)
    {
        if (a[i].name() == name)
        {
            return i;
        }
    }
    return -1;
}

inline void split(std::vector<DimData> &a, const std::string &owner,
                  const Var &old_var, const Var &outer, const Var &inner)
{
    int pos = dim_pos(a, old_var.name());
    if (pos < 0)
    {
        throw std::runtime_error("micro_halide: split: \"" + owner +
                                 "\" has no dimension \"" + old_var.name() + "\"");
    }
    a.erase(a.begin() + pos);
    a.insert(a.begin() + pos, DimData(outer.name())); // outer goes to old's slot first ...
    a.insert(a.begin() + pos, DimData(inner.name())); // ... then inner pushed inside it
}

inline void fuse(std::vector<DimData> &a, const std::string &owner,
                 const Var &inner, const Var &outer, const Var &fused)
{
    int ipos = dim_pos(a, inner.name());
    int opos = dim_pos(a, outer.name());
    if (ipos < 0)
    {
        throw std::runtime_error("micro_halide: fuse: \"" + owner +
                                 "\" has no dimension \"" + inner.name() + "\"");
    }
    if (opos < 0)
    {
        throw std::runtime_error("micro_halide: fuse: \"" + owner +
                                 "\" has no dimension \"" + outer.name() + "\"");
    }
    int hi = std::max(ipos, opos);
    int lo = std::min(ipos, opos);
    a.erase(a.begin() + hi);
    a.erase(a.begin() + lo);
    int insert_pos = (opos < ipos) ? ipos - 1 : ipos;
    a.insert(a.begin() + insert_pos, DimData(fused.name()));
}

inline void reorder(std::vector<DimData> &a, const std::string &owner,
                    const std::vector<std::string> &names)
{
    std::vector<int> slots;
    for (const std::string &n : names)
    {
        int pos = dim_pos(a, n);
        if (pos < 0)
        {
            throw std::runtime_error("micro_halide: reorder: \"" + owner +
                                     "\" has no dimension \"" + n + "\"");
        }
        for (int s : slots)
        {
            if (a[s].name() == n)
            {
                throw std::runtime_error("micro_halide: reorder: dimension \"" + n +
                                         "\" named more than once");
            }
        }
        slots.push_back(pos);
    }
    std::vector<DimData> requested;
    for (const std::string &n : names)
    {
        requested.push_back(a[dim_pos(a, n)]);
    }
    std::sort(slots.begin(), slots.end());
    for (size_t i = 0; i < slots.size(); i++)
    {
        a[slots[i]] = requested[i];
    }
}
} // namespace dimlist

// Declaration rank of an RVar within its RDom (innermost first): r.x < r.y <
// r.z < r.w. A multi-dim RDom names its RVars "<base>$x", "$y", "$z", "$w"; a
// 1-D RDom's single RVar is the bare base name (rank 0). Used to put the
// first-declared reduction dimension innermost (loopdoc.md section 3).
inline int rvar_decl_rank(const std::string &name)
{
    auto pos = name.rfind('$');
    if (pos == std::string::npos)
    {
        return 0; // 1-D RDom: bare name
    }
    char c = (pos + 1 < name.size()) ? name[pos + 1] : 'x';
    switch (c)
    {
    case 'x': return 0;
    case 'y': return 1;
    case 'z': return 2;
    case 'w': return 3;
    default: return 4;
    }
}

// ---------------------------------------------------------------------------
// FuncRef: the result of Func::operator(). Acts as an lvalue to define a Func
// (operator=) and as an rvalue Expr to read it.
// ---------------------------------------------------------------------------
class FuncRef
{
  public:
    std::shared_ptr<FuncContents> func;
    std::vector<Var> vars;     // the pure Vars used as arguments (LHS use)
    std::vector<bool> is_var;  // whether each arg was a plain Var
    std::vector<Expr> arg_exprs;

    void add_arg(const Var &v)
    {
        vars.push_back(v);
        is_var.push_back(true);
        arg_exprs.push_back(Expr(v));
    }
    void add_arg(const Expr &e)
    {
        vars.push_back(Var(""));
        is_var.push_back(false);
        arg_exprs.push_back(e);
    }

    // All Funcs read by the RHS plus the LHS index expressions (deduped),
    // excluding `self` (a Func's update may read itself; that is not a
    // producer edge).
    std::vector<std::shared_ptr<FuncContents>> collect_producers(const Expr &rhs) const
    {
        std::vector<std::shared_ptr<FuncContents>> deps = rhs.deps;
        for (const Expr &a : arg_exprs)
        {
            deps.insert(deps.end(), a.deps.begin(), a.deps.end());
        }
        std::vector<std::shared_ptr<FuncContents>> out;
        std::set<FuncContents *> seen{func.get()}; // exclude self
        for (auto &d : deps)
        {
            if (d && seen.insert(d.get()).second)
            {
                out.push_back(d);
            }
        }
        return out;
    }

    // Distinct RVar names referenced by the LHS index expressions and the RHS,
    // in first-appearance order.
    std::vector<std::string> collect_rvars(const Expr &rhs) const
    {
        std::vector<std::string> all = rhs.rvars;
        for (const Expr &a : arg_exprs)
        {
            all.insert(all.end(), a.rvars.begin(), a.rvars.end());
        }
        std::vector<std::string> out;
        std::set<std::string> seen;
        for (const std::string &n : all)
        {
            if (seen.insert(n).second)
            {
                out.push_back(n);
            }
        }
        return out;
    }

    // Record one update stage (loopdoc.md section 10). Captures the raw,
    // type-distinguished data; the micro-agent turns it into a dimension list.
    void record_update(const Expr &rhs)
    {
        std::vector<Var> pure_args;
        StageData u;
        for (size_t i = 0; i < vars.size(); i++)
        {
            if (is_var[i])
            {
                pure_args.push_back(vars[i]); // a bare Var LHS arg = free dim
            }
        }
        std::vector<std::string> rvars = collect_rvars(rhs);
        u.producers = collect_producers(rhs);
        // Build this stage's DEFAULT dimension list (loopdoc.md section 3),
        // innermost-first: the RVars are innermost -- and WITHIN the RVars the
        // first-declared dimension (r.x) is the INNERMOST loop (matching the Var
        // convention that the first dimension varies fastest). The free pure
        // Vars sit OUTSIDE the RVars (first LHS arg innermost among the pures,
        // i.e. pure_args is already innermost-first).
        //
        // The captured rvar names are in first-appearance order, which need not
        // equal RDom declaration order; sort them into declaration order (r.x,
        // r.y, r.z, r.w) so the first-declared lands at dims[0] (innermost).
        std::vector<std::string> ordered_rvars = rvars;
        std::stable_sort(ordered_rvars.begin(), ordered_rvars.end(),
                         [](const std::string &a, const std::string &b) {
                             return rvar_decl_rank(a) < rvar_decl_rank(b);
                         });
        for (const std::string &n : ordered_rvars)
        {
            u.dims.push_back(DimData(n));
            u.rvars.insert(n); // mark this dim as an RVar (reduction) loop
        }
        for (const Var &v : pure_args)
        {
            u.dims.push_back(DimData(v.name()));
        }
        // Union this stage's producers into the Func's overall producer set
        // (used for realization order and cross-stage legality).
        std::set<FuncContents *> have;
        for (auto &p : func->producers)
        {
            have.insert(p.get());
        }
        for (auto &p : u.producers)
        {
            if (have.insert(p.get()).second)
            {
                func->producers.push_back(p);
            }
        }
        func->stages.push_back(std::move(u));
    }

    // Define or update the Func: f(x, y, ...) = rhs;
    // The first definition is the pure (initial) stage; any later assignment is
    // an update definition (loopdoc.md section 10).
    void operator=(const Expr &rhs)
    {
        if (func->defined)
        {
            record_update(rhs);
            return;
        }
        // Stage 0 (pure/initial definition): its dimension list is the pure
        // args, and its reads are the producers. pure_args mirrors dims for
        // uniformity with update stages (no RVars in a pure definition).
        StageData s0;
        s0.dims = DimData::from_var_list(vars);
        s0.producers = collect_producers(rhs);
        func->stages.push_back(std::move(s0));
        func->producers = func->stages[0].producers;
        func->defined = true;
    }
    void operator=(const FuncRef &rhs)
    {
        *this = Expr(rhs);
    }

    // f(...) += rhs;  /  f(...) *= rhs;  -- both add an update stage. The user's
    // rhs is the increment/factor (the implicit self-read is not a producer).
    void operator+=(const Expr &rhs)
    {
        record_update(rhs);
    }
    void operator*=(const Expr &rhs)
    {
        record_update(rhs);
    }
};

inline Expr::Expr(const FuncRef &ref)
{
    deps.push_back(ref.func);
    for (const Expr &a : ref.arg_exprs)
    {
        deps.insert(deps.end(), a.deps.begin(), a.deps.end());
        rvars.insert(rvars.end(), a.rvars.begin(), a.rvars.end());
    }
}

// ---------------------------------------------------------------------------
// ImageParam: an input buffer. It is a leaf: it is never realized and never
// appears in the loop nest. Funcs may read from it, which terminates a
// dependency chain (the buffer is simply already there).
// ---------------------------------------------------------------------------
class ImageParam
{
    std::string _name;
    int _dims;

  public:
    ImageParam(Type, int d, std::string n) : _name(std::move(n)), _dims(d)
    {
    }

    int dimensions() const
    {
        return _dims;
    }

    const std::string &name() const
    {
        return _name;
    }

    // Reading an ImageParam is not a Func dependency (it is already stored),
    // but its index expressions still carry RVars (and any Func reads), which
    // must propagate so an update definition can see which reduction loops it
    // uses (e.g. in(x, r)).
    template <typename... Args>
    Expr operator()(Args... args) const
    {
        Expr acc;
        (void)std::initializer_list<int>{(acc = combine(acc, Expr(args)), 0)...};
        return acc;
    }
};

class Func;
class Stage;

// ---------------------------------------------------------------------------
// Common code for scheduling operators valid both on Func (program the
// pure stage) and Stage (program an update stage).
// ---------------------------------------------------------------------------
template <typename Derived>
class FuncStageImpl
{
  public:
    std::shared_ptr<FuncContents> contents;
    int stage_index;  // 0 for pure stage, 1 + n for n-th update stage.

    FuncStageImpl(std::shared_ptr<FuncContents> _contents, int _stage_index)
      : contents(std::move(_contents))
      , stage_index(_stage_index)
    {
    }

    // split(old, outer, inner, factor): replace `old` with two dimensions --
    // `inner` (innermost, at old's former slot) and `outer` just outside it.
    // [x, y] under split(x, xo, xi, 8) -> [xi, xo, y]. One extra `for`.
    Derived &split(const Var &old_var, const Var &outer, const Var &inner, int factor)
    {
        (void)factor; // bound is normalized away by the harness
        dimlist::split(dims(), owner(), old_var, outer, inner);
        return static_cast<Derived&>(*this);
    }

    // fuse(inner, outer, fused): remove `inner` and `outer`, place a single
    // `fused` dimension at inner's former position. [x, y] under fuse(x, y, xy)
    // -> [xy]. One fewer `for`.
    Derived &fuse(const Var &inner, const Var &outer, const Var &fused)
    {
        dimlist::fuse(dims(), owner(), inner, outer, fused);
        return static_cast<Derived&>(*this);
    }

    // tile(x, y, xo, yo, xi, yi, xf, yf): split(x,xo,xi,xf); split(y,yo,yi,yf);
    // reorder(xi, yi, xo, yo). [x, y] -> [xi, yi, xo, yo]. Two extra `for`s.
    Derived &tile(const Var &x, const Var &y,
                  const Var &xo, const Var &yo,
                  const Var &xi, const Var &yi,
                  int xfactor, int yfactor)
    {
        (void)xfactor; (void)yfactor;
        dimlist::split(dims(), owner(), x, xo, xi);
        dimlist::split(dims(), owner(), y, yo, yi);
        dimlist::reorder(dims(), owner(), {xi.name(), yi.name(), xo.name(), yo.name()});
        return static_cast<Derived&>(*this);
    }

    // reorder(v_inner, ..., v_outer): lists dimensions innermost first and
    // permutes ONLY the listed dimensions among the slots they currently
    // occupy; unnamed dimensions keep their position. Each listed dimension
    // must exist and be named at most once.
    // accepts Vars, RVars, or a 1-D RDom (anything with .name()).
    template <typename... Vars>
    Derived &reorder(const Vars &...vars)
    {
        std::vector<std::string> names{vars.name()...};
        dimlist::reorder(dims(), owner(), names);
        return static_cast<Derived&>(*this);
    }

    // This update stage's dimension list (loopdoc.md section 10): the same
    // ordered list the printer walks, so transforming it here rewrites only
    // THIS stage's loops. RVars sit in the list just like Vars.
    std::vector<DimData> &dims()
    {
        return contents->stages[stage_index].dims; // stages[0] is pure; update i -> stage i+1
    }
    const std::string &owner() const
    {
        return contents->name;
    }
};

// ---------------------------------------------------------------------------
// Func: a handle to a (shared) FuncContents.
// ---------------------------------------------------------------------------
class Func: public FuncStageImpl<Func>
{
  public:
    Func(): FuncStageImpl(std::make_shared<FuncContents>(), 0)
    {
    }

    explicit Func(std::string name): FuncStageImpl(std::make_shared<FuncContents>(), 0)
    {
        contents->name = std::move(name);
    }

    const std::string &name() const
    {
        return contents->name;
    }

    template <typename... Args>
    FuncRef operator()(Args... args) const
    {
        FuncRef ref;
        ref.func = contents;
        (ref.add_arg(args), ...);
        return ref;
    }

    Func &compute_root()
    {
        contents->level = FuncContents::Level::Root;
        contents->at_func.reset();
        return *this;
    }

    Func &compute_at(const Func &f, const Var &var)
    {
        contents->level = FuncContents::Level::At;
        contents->at_func = f.contents;
        contents->at_var = var.name();
        return *this;
    }
    // A reduction variable / 1-D RDom names a loop too, so it can be a
    // compute_at site (loopdoc.md section 10). Both expose .name().
    Func &compute_at(const Func &f, const RVar &r)
    {
        return compute_at(f, Var(r.name()));
    }
    Func &compute_at(const Func &f, const RDom &r)
    {
        return compute_at(f, Var(r.name()));
    }

    // store_at / store_root: record the store level. See the note on
    // FuncContents -- the loop-nest effect (the `store` node) and legality are
    // for the micro-agent to implement from loopdoc.md section 8.
    Func &store_at(const Func &f, const Var &var)
    {
        contents->has_store_level = true;
        contents->store_is_root = false;
        contents->store_func = f.contents;
        contents->store_var = var.name();
        return *this;
    }

    Func &store_root()
    {
        contents->has_store_level = true;
        contents->store_is_root = true;
        contents->store_func.reset();
        contents->store_var.clear();
        return *this;
    }

    // hoist_storage / hoist_storage_root: record the hoist-storage level. This
    // has NO effect on the printed nest; only legality (for the micro-agent to
    // implement from loopdoc.md section 8) depends on it.
    Func &hoist_storage(const Func &f, const Var &var)
    {
        contents->has_hoist_level = true;
        contents->hoist_is_root = false;
        contents->hoist_func = f.contents;
        contents->hoist_var = var.name();
        return *this;
    }

    Func &hoist_storage_root()
    {
        contents->has_hoist_level = true;
        contents->hoist_is_root = true;
        contents->hoist_func.reset();
        contents->hoist_var.clear();
        return *this;
    }

    // Handle to an update stage for per-stage scheduling: f.update(i) schedules
    // update stage s(i+1) (loopdoc.md section 10). Returns a Stage (below).
    class Stage update(int i = 0) const;

    void print_loop_nest();
};

// ---------------------------------------------------------------------------
// Stage: a handle to one update stage's schedule, returned by Func::update(i).
// The loop transforms below are STUBS (no-ops) provided by the main agent only
// so the examples COMPILE. Implementing their per-stage effect -- rewriting
// THAT stage's dimension list (which may contain RVars) -- is the micro-agent's
// task, from loopdoc.md sections 6 and 10.
// ---------------------------------------------------------------------------
class Stage: public FuncStageImpl<Stage>
{
  public:
    Stage(std::shared_ptr<FuncContents> f, int i) : FuncStageImpl(std::move(f), i + 1)
    {
    }

    // rfactor (loopdoc.md section 12): factor THIS update stage's associative
    // reduction into a new intermediate Func plus a rewritten merge stage. It
    // CREATES a new Func (returned) and MUTATES this stage. This is a STUB
    // provided by the main agent only so examples compile; building the
    // intermediate's stages and rewriting the merge is the documented behavior
    // (loopdoc.md section 12) the micro-agent implements.
    Func rfactor(const RVar &r, const Var &v)
    {
        return rfactor(std::vector<std::pair<RVar, Var>>{{r, v}});
    }
    Func rfactor(const std::vector<std::pair<RVar, Var>> &preserved);
};

inline Stage Func::update(int i) const
{
    return Stage(contents, i);
}

// rfactor (loopdoc.md section 12): factor THIS update stage's associative
// reduction into a fresh intermediate Func plus a rewritten merge stage.
//
// Given the original Func `f` with pure-stage dims P (innermost first) and the
// chosen update stage with dim list U (innermost first, containing RVars and
// free Vars), and a list of preserved pairs {RVar -> Var}:
//
//   * The intermediate Func `<f>_intm` is built with two stages:
//       - pure stage: dims = P, then the new pure Vars in preserved order,
//         the new vars OUTERMOST (so appended to the innermost-first list).
//       - update stage: a COPY of U with each preserved RVar name replaced
//         in place by its new pure Var name. Non-preserved RVars stay as
//         reduction loops; the loop order is otherwise unchanged. It reads
//         whatever the original update read (its producers).
//   * The original chosen update stage is REWRITTEN into the merge: its dim
//     list keeps the free Vars and the preserved RVars (still RVars) and
//     DROPS the non-preserved RVars. Its only producer becomes the
//     intermediate (so `intm` is a producer of `f`).
inline Func Stage::rfactor(const std::vector<std::pair<RVar, Var>> &preserved)
{
    if (stage_index == 0)
    {
        throw std::runtime_error(
            "micro_halide: rfactor may only be called on an update stage, "
            "not the pure stage (loopdoc.md section 12)");
    }

    FuncContents *orig = contents.get();
    StageData &update = orig->stages[stage_index];

    // Map preserved RVar name -> new pure Var name, and the set of preserved
    // RVar names (to decide which dims are dropped in the merge).
    std::map<std::string, std::string> rvar_to_var;
    std::set<std::string> preserved_rvars;
    for (const auto &p : preserved)
    {
        rvar_to_var[p.first.name()] = p.second.name();
        preserved_rvars.insert(p.first.name());
    }

    // ---- Build the intermediate Func --------------------------------------
    Func intm(orig->name + "_intm");
    intm.contents->defined = true;

    // Intermediate pure stage: original pure dims, then new pure Vars in
    // preserved order, new vars outermost (appended to the innermost-first
    // list).
    StageData intm_pure;
    intm_pure.dims = orig->stages[0].dims;
    for (const auto &p : preserved)
    {
        intm_pure.dims.push_back(DimData(p.second.name()));
    }
    // The pure init reads nothing (it is `= 0`).

    // Intermediate update stage: copy of the original update dim list with
    // each preserved RVar replaced in place by its new pure Var.
    StageData intm_update;
    for (const DimData &d : update.dims)
    {
        auto it = rvar_to_var.find(d.name());
        if (it != rvar_to_var.end())
        {
            // A preserved RVar becomes a pure Var in the intermediate.
            intm_update.dims.push_back(DimData(it->second));
        }
        else
        {
            intm_update.dims.push_back(d);
            // A non-preserved RVar stays a reduction loop here.
            if (update.rvars.count(d.name()))
            {
                intm_update.rvars.insert(d.name());
            }
        }
    }
    // It reads whatever the original update read.
    intm_update.producers = update.producers;

    intm.contents->stages.push_back(std::move(intm_pure));
    intm.contents->stages.push_back(std::move(intm_update));
    // The intermediate's overall producers are those of its update stage.
    intm.contents->producers = intm.contents->stages[1].producers;

    // ---- Rewrite the original chosen update stage into the merge ----------
    std::vector<DimData> merged;
    std::set<std::string> merged_rvars;
    for (const DimData &d : update.dims)
    {
        bool is_rvar = update.rvars.count(d.name()) != 0;
        if (is_rvar && !preserved_rvars.count(d.name()))
        {
            continue; // non-preserved RVar: lifted into the intermediate
        }
        merged.push_back(d); // free Var, or preserved RVar (stays an RVar here)
        if (is_rvar)
        {
            merged_rvars.insert(d.name());
        }
    }
    update.dims = std::move(merged);
    update.rvars = std::move(merged_rvars);
    // The merge now reads only the intermediate.
    update.producers = {intm.contents};

    // The intermediate becomes a producer of the original Func. Add it to the
    // Func's overall producer set if not already present.
    {
        bool have = false;
        for (auto &p : orig->producers)
        {
            if (p.get() == intm.contents.get())
            {
                have = true;
                break;
            }
        }
        if (!have)
        {
            orig->producers.push_back(intm.contents);
        }
    }

    return intm;
}

// ---------------------------------------------------------------------------
// micro_halide_collapses(...): declare that the named loops of a STAGE are
// elided by Halide (their required extent is 1). This is an annotation of
// ground truth supplied by the example author, NOT something micro_halide
// derives -- predicting it requires bounds inference, which loopdoc.md keeps
// out of scope. Under real Halide these are no-op stubs (see the halide_compat
// header); only the loop *structure* is what the docs teach and validate.
//
// Mirroring the Halide scheduling API, collapse is declared PER STAGE:
//   micro_halide_collapses(f, {vars})            -> the pure stage (stage 0)
//   micro_halide_collapses(f.update(N), {vars})  -> update stage N (= stage N+1)
// ---------------------------------------------------------------------------
inline void micro_halide_collapses(const Func &f, std::initializer_list<Var> vars)
{
    for (const Var &v : vars)
    {
        f.contents->stages[0].collapsed.insert(v.name());
    }
}
inline void micro_halide_collapses(const Stage &s, std::initializer_list<Var> vars)
{
    for (const Var &v : vars)
    {
        s.contents->stages[s.stage_index].collapsed.insert(v.name());
    }
}

// ---------------------------------------------------------------------------
// BoundaryConditions: helpers that wrap an input in a Func. Typing/bounds are
// irrelevant here, so the wrapper is just a fresh Func of the same
// dimensionality that depends on nothing realizable.
// ---------------------------------------------------------------------------
namespace BoundaryConditions
{
inline Func repeat_edge(const ImageParam &im)
{
    Func f("repeat_edge");
    StageData s0; // stage 0: pure definition, no producers
    for (int i = 0; i < im.dimensions(); i++)
    {
        s0.dims.push_back(DimData("_" + std::to_string(i)));
    }
    f.contents->stages.push_back(std::move(s0));
    f.contents->defined = true;
    return f;
}
} // namespace BoundaryConditions

// ===========================================================================
// Loop-nest generation.
// ===========================================================================
namespace internal
{

struct LoopNestPrinter
{
    std::ostream &out;

    // A loop level inside a particular STAGE of a host: (host, stage, var-name).
    // With update definitions (loopdoc.md section 10) a host emits one loop nest
    // per stage inside a single `produce`, so an injection/store site is pinned
    // to a specific stage (an RVar loop, for instance, exists only in its own
    // stage).
    using SiteKey = std::tuple<FuncContents *, int, std::string>;

    // funcs computed_at a given stage-loop level, in realization order.
    std::map<SiteKey, std::vector<FuncContents *>> children_at;

    // funcs whose `store` node opens at a given stage-loop level (store_at with
    // a store level outer to the compute level), in realization order. A
    // `store f:` node here wraps everything emitted deeper at that level -- the
    // host loops between the store and compute levels, and f's own
    // produce/consume at its compute level.
    std::map<SiteKey, std::vector<FuncContents *>> store_at_level;

    // funcs with store_root() (and a non-root compute level): their `store` node
    // is the outermost node, wrapping the whole top-level chain.
    std::vector<FuncContents *> store_root_funcs;

    // First-visitation index of each Func (pre-order DFS from the output through
    // producers in definition order). Used as a tie-breaker in realization
    // order (see sort_key).
    std::map<FuncContents *, std::uint64_t> visit_order;

    explicit LoopNestPrinter(std::ostream &o) : out(o)
    {
    }

    static std::string pad(int indent)
    {
        return std::string(indent, ' ');
    }

    // Name prefix used for the realization-order tie-break: drop any "$..."
    // uniqueness suffix, then any trailing digits. (Matches Halide's
    // sort_funcs_by_name_and_counter in RealizationOrder.cpp.)
    static std::string name_prefix(const std::string &s)
    {
        std::string p = s.substr(0, s.find('$'));
        while (!p.empty() && std::isdigit(static_cast<unsigned char>(p.back())))
        {
            p.pop_back();
        }
        return p;
    }

    // Pre-order DFS from the output recording first-visitation order. Producers
    // are walked in definition (first-appearance) order, mirroring Halide's
    // populate_environment_helper.
    void compute_visit_order(FuncContents *f, std::set<FuncContents *> &seen,
                             std::uint64_t &counter)
    {
        if (!seen.insert(f).second)
        {
            return;
        }
        visit_order[f] = counter++;
        for (auto &p : f->producers)
        {
            compute_visit_order(p.get(), seen, counter);
        }
    }

    // The order two sibling producers of the same consumer are realized in:
    // primarily alphabetical by name prefix, then by first-visitation order,
    // then by full name. This is NOT the left-to-right order in the defining
    // expression.
    std::tuple<std::string, std::uint64_t, std::string> sort_key(FuncContents *f)
    {
        return {name_prefix(f->name), visit_order[f], f->name};
    }

    // Post-order DFS over producers (producers before consumers), visiting a
    // Func's producers in realization-order tie-break order.
    void realization_order(FuncContents *f, std::set<FuncContents *> &visited,
                           std::vector<FuncContents *> &order)
    {
        if (!visited.insert(f).second)
        {
            return;
        }
        std::vector<FuncContents *> prods;
        for (auto &p : f->producers)
        {
            prods.push_back(p.get());
        }
        std::sort(prods.begin(), prods.end(),
                  [this](FuncContents *a, FuncContents *b) { return sort_key(a) < sort_key(b); });
        for (FuncContents *p : prods)
        {
            realization_order(p, visited, order);
        }
        order.push_back(f);
    }

    // --- Schedule validation (rejects illegal compute_at, like Halide) -------

    // A Func is PURE iff it has only its initial definition -- no update stages
    // (loopdoc.md section 4). A non-pure Func has one or more updates.
    static bool is_pure(FuncContents *f)
    {
        return f->stages.size() <= 1;
    }

    // A Func is NON-REALIZED (textually substituted, no block of its own) iff it
    // is at the default inline level AND pure (loopdoc.md section 4/5). A
    // non-pure func cannot be substituted, so even at the inline level it is
    // REALIZED, at the innermost point of each use (loopdoc.md section 11).
    static bool is_non_realized(FuncContents *f)
    {
        return f->level == FuncContents::Level::Inline && is_pure(f);
    }

    // A Func is REALIZED iff it gets its own produce block: anything that is not
    // non-realized. This includes compute_root, compute_at, and the awkward
    // inline-non-pure default (loopdoc.md section 11).
    static bool is_realized(FuncContents *f)
    {
        return !is_non_realized(f);
    }

    // ---- Per-stage views (loopdoc.md section 10) -------------------------
    // A Func has stage 0 (the pure definition) plus one stage per update; each
    // stage has its own dimension list, collapse-set, and producer set.

    static int num_stages(FuncContents *f)
    {
        return (int)f->stages.size();
    }

    static const std::vector<DimData> &stage_dims(FuncContents *f, int s)
    {
        return f->stages[s].dims;
    }

    static const std::set<std::string> &stage_collapsed(FuncContents *f, int s)
    {
        return f->stages[s].collapsed;
    }

    static const std::vector<std::shared_ptr<FuncContents>> &stage_producers(FuncContents *f, int s)
    {
        return f->stages[s].producers;
    }

    // Index of a Var name within stage s's dimension list (dim0 = innermost), -1
    // if absent.
    static int stage_dim_index(FuncContents *g, int s, const std::string &var)
    {
        const std::vector<DimData> &d = stage_dims(g, s);
        for (int i = 0; i < (int)d.size(); i++)
        {
            if (d[i].name() == var)
            {
                return i;
            }
        }
        return -1;
    }

    // The stage of host g whose dimension list contains `var`. compute_at/store_at
    // name a single loop var; with updates a name may appear in several stages
    // (e.g. a pure Var carried into an update). We pick the lowest-index stage
    // that has it; an RVar name is unique to one stage. Returns -1 if no stage
    // has it.
    static int resolve_stage(FuncContents *g, const std::string &var)
    {
        for (int s = 0; s < num_stages(g); s++)
        {
            if (stage_dim_index(g, s, var) >= 0)
            {
                return s;
            }
        }
        return -1;
    }

    // Index of a Var name among a Func's pure (stage 0) dims (dim0 = innermost),
    // or -1. (Used by store/hoist legality, whose hosts are pure Funcs.)
    static int dim_index(FuncContents *g, const std::string &var)
    {
        const std::vector<DimData> &a = g->stages[0].dims;
        for (int i = 0; i < (int)a.size(); i++)
        {
            if (a[i].name() == var)
            {
                return i;
            }
        }
        return -1;
    }

    // Does any stage of realized Func h reference f? (Used as a cheap "is h a
    // reader of f at all" test; the precise per-stage version is below.)
    bool any_stage_reads(FuncContents *h, FuncContents *f)
    {
        for (int s = 0; s < num_stages(h); s++)
        {
            if (stage_reads(h, s, f))
            {
                return true;
            }
        }
        return false;
    }

    // Does STAGE s of realized Func h reference f? f is referenced if it is a
    // direct producer of that stage, or reachable from it through a chain of
    // *inlined* producers (which are substituted in). A realized intermediate
    // stops the search: that stage then reads the intermediate, not f.
    bool stage_reads(FuncContents *h, int s, FuncContents *f)
    {
        for (auto &p : stage_producers(h, s))
        {
            if (p.get() == f)
            {
                return true;
            }
            std::set<FuncContents *> seen;
            if (is_non_realized(p.get()) && inlined_reads(p.get(), f, seen))
            {
                return true;
            }
        }
        return false;
    }

    // Does an inlined Func b (any of its stages) reach f through inlined chains?
    bool inlined_reads(FuncContents *b, FuncContents *f, std::set<FuncContents *> &seen)
    {
        if (!seen.insert(b).second)
        {
            return false;
        }
        for (auto &p : b->producers)
        {
            if (p.get() == f)
            {
                return true;
            }
            if (is_non_realized(p.get()) && inlined_reads(p.get(), f, seen))
            {
                return true;
            }
        }
        return false;
    }

    // Does the loop body at site (host, hs, var) USE f -- directly, or
    // TRANSITIVELY through another producer realized inside that body
    // (loopdoc.md section 7, "Computing at an indirect consumer's loop")?
    //
    // The injection rule is "inject f wherever the loop body at the chosen level
    // uses f", applied to the body AFTER inner producers have been placed: once
    // some producer g sits at (or within) this site and g reads f, the body here
    // calls f through g, so f's use lands here. "Reads f" is itself transitive,
    // so we recurse (g may read f only through a further intermediate placed in
    // the same body).
    //
    // A producer g is "realized in the body at (host, hs, var)" iff g is computed
    // at this host stage's `var` loop or an INNER one (g's body is then nested
    // inside this loop). g at an OUTER loop of the host is NOT in this body --
    // this site lives in g's `consume`, after g (the neg_transitive_..._inner
    // case): such a g does not pull f in here.
    // Guard against mutual recursion: two producers g, h both filed at (host, v)
    // can each ask whether the other is present in host stage hs ("is g in this
    // body?" -> "is h in this body?" -> ...). The "is X present in this stage"
    // question is monotone, so treating an in-progress query as "not yet known to
    // be present" (false) is the correct fixpoint and breaks the cycle.
    std::set<std::tuple<FuncContents *, int, FuncContents *>> body_uses_active_;

    bool body_uses(FuncContents *host, int hs, const std::string &var, FuncContents *f,
                   const std::vector<FuncContents *> &order)
    {
        if (stage_reads(host, hs, f))
        {
            return true;
        }
        int ivar = stage_dim_index(host, hs, var);
        if (ivar < 0)
        {
            return false;
        }
        auto key = std::make_tuple(host, hs, f);
        if (!body_uses_active_.insert(key).second)
        {
            // Already evaluating this exact (host, stage, f) query higher in the
            // recursion -- treat as not (yet) present to terminate the cycle.
            return false;
        }
        bool result = false;
        for (FuncContents *g : order)
        {
            if (g == f || g == host || !is_realized(g) ||
                g->level != FuncContents::Level::At || g->at_func.get() != host)
            {
                continue;
            }
            // g must be realized in THIS stage's body, at var or an inner loop.
            int ig = stage_dim_index(host, hs, g->at_var);
            if (ig < 0 || ig > ivar)
            {
                continue;
            }
            // ...AND g must actually be injected INTO this host stage. The level
            // (host, g->at_var) names a g->at_var loop in EVERY stage of host, but
            // g lands only in the stages whose body uses g (loopdoc.md section 7:
            // "an intermediate g is realized in a stage's body only if THAT stage
            // actually uses g"). So the pull-in stacks per stage: f appears in
            // stage hs iff hs's body uses g at the var loop AND g uses f. Without
            // this check the rfactor intermediate's pure stage (which reads
            // neither g nor f) would wrongly pull f in just because it shares the
            // var loop with the reducing stage (rfactor_indirect_at_intm).
            if (!body_uses(host, hs, g->at_var, g, order))
            {
                continue;
            }
            // Does g (transitively) use f? g's body is its own loop nest; f's use
            // through g is wherever g reads f, which is enclosed by g's loops --
            // and g is in this host body, so f's use is too.
            if (g_uses_f(g, f, order))
            {
                result = true;
                break;
            }
        }
        body_uses_active_.erase(key);
        return result;
    }

    // Does realized producer g use f anywhere in its own multi-stage body --
    // directly (any stage reads f) or transitively (through a further producer
    // realized inside g)?
    bool g_uses_f(FuncContents *g, FuncContents *f, const std::vector<FuncContents *> &order)
    {
        for (int gs = 0; gs < num_stages(g); gs++)
        {
            if (stage_reads(g, gs, f))
            {
                return true;
            }
            // A producer p realized inside g's stage gs (at any of its loops, or
            // its leaf) that uses f makes g use f. We approximate g's loops by its
            // dimension list; any p computed at (g, gs, <any dim>) is inside g's
            // body. Walk each dim as a candidate body site.
            const std::vector<DimData> &d = stage_dims(g, gs);
            for (const DimData &dv : d)
            {
                if (body_uses(g, gs, dv.name(), f, order))
                {
                    return true;
                }
            }
        }
        return false;
    }

    // Is realized reader h enclosed by (i.e. computed inside) the loop (g, v)?
    bool enclosed_by(FuncContents *h, FuncContents *g, const std::string &v)
    {
        if (h == g)
        {
            // The host reads f within its own body, which runs inside this loop.
            return true;
        }
        if (h->level == FuncContents::Level::At)
        {
            if (h->at_func.get() == g)
            {
                // h is computed at (g, h->at_var); it sits inside (g, v) iff
                // h->at_var is v or an inner loop of g (inner = lower index).
                int ih = dim_index(g, h->at_var);
                int iv = dim_index(g, v);
                return ih >= 0 && iv >= 0 && ih <= iv;
            }
            return enclosed_by(h->at_func.get(), g, v);
        }
        return false; // h is at root (and not g): not inside g's loop
    }

    // ---- Stage-aware enclosure (loopdoc.md sections 8 + 10) --------------
    //
    // A "site" is a loop level (g, gs, gv): host g's stage gs, loop named gv. A
    // producer computed there can only feed uses that this loop encloses.

    // Does loop (g, gs, gv) enclose (== is the same as, or outer to) loop
    // (h, hs, hv)?
    bool site_encloses_loop(FuncContents *g, int gs, const std::string &gv,
                            FuncContents *h, int hs, const std::string &hv)
    {
        if (g == h)
        {
            // (g, gv) is a family of loops, one per stage of g (loopdoc.md
            // section 7). The member in stage hs encloses loop (h, hs, hv) iff
            // that same stage has gv and gv is the same or an outer loop. (The
            // passed-in gs is irrelevant for a same-Func comparison: we ask
            // whether the family's member in *hs* encloses hv.)
            int ig = stage_dim_index(g, hs, gv);
            int ih = stage_dim_index(h, hs, hv);
            // Outer = higher dim index (dim0 innermost). gv encloses hv iff it is
            // the same or an outer loop.
            return ig >= 0 && ih >= 0 && ig >= ih;
        }
        // h is realized at its own compute level; follow it outward.
        if (h->level == FuncContents::Level::At)
        {
            FuncContents *host = h->at_func.get();
            int host_s = resolve_stage(host, h->at_var);
            if (host_s < 0)
            {
                return false;
            }
            return site_encloses_loop(g, gs, gv, host, host_s, h->at_var);
        }
        return false; // h at root and not g: not inside g's loop
    }

    // Does site (g, gs, gv) enclose the body of stage hs of reader h (i.e. the
    // point where h reads f)?
    bool site_encloses_use(FuncContents *g, int gs, const std::string &gv,
                           FuncContents *h, int hs)
    {
        if (g == h)
        {
            // The use is in g's own stage hs. The level (g, gv) is a FAMILY of
            // loops, one per stage of g that has gv (loopdoc.md section 7); the
            // member living in stage hs encloses this use iff stage hs actually
            // has a loop named gv. (So a loop shared by every using stage --
            // e.g. a pure Var carried into the updates -- is a legal site, while
            // an RVar that exists only in one stage cannot enclose a use in a
            // different stage.)
            return stage_dim_index(g, hs, gv) >= 0;
        }
        if (h->level == FuncContents::Level::At)
        {
            FuncContents *host = h->at_func.get();
            int host_s = resolve_stage(host, h->at_var);
            if (host_s < 0)
            {
                return false;
            }
            // h's stage hs runs just inside h's compute loop (host, host_s,
            // at_var); enclosed iff the site is that loop or outer to it.
            return site_encloses_loop(g, gs, gv, host, host_s, h->at_var);
        }
        return false; // h at root and not g
    }

    [[noreturn]] static void fail(FuncContents *f, const std::string &why)
    {
        throw std::runtime_error("micro_halide: invalid schedule for Func \"" + f->name +
                                 "\": " + why);
    }

    // Validate every compute_at in the pipeline. `funcs` is every reachable Func.
    void validate(const std::vector<FuncContents *> &funcs)
    {
        for (FuncContents *f : funcs)
        {
            // -- Store-level legality (loopdoc.md section 8) -------------------
            if (f->has_store_level)
            {
                // store_at/store_root requires a non-inline compute level.
                if (f->level == FuncContents::Level::Inline)
                {
                    fail(f, "has a store level (store_at/store_root) but is inlined; "
                            "Funcs that use store_at must also call compute_at/compute_root");
                }
                if (!f->store_is_root)
                {
                    FuncContents *sg = f->store_func.get();
                    const std::string &sv = f->store_var;
                    if (!sg || !is_realized(sg))
                    {
                        fail(f, "store_at host is inlined/undefined, so it has no loop");
                    }
                    if (dim_index(sg, sv) < 0)
                    {
                        fail(f, "store_at loop variable does not exist in the host Func");
                    }
                    // The store level must ENCLOSE the compute level: same loop
                    // or an outer one.
                    if (f->level == FuncContents::Level::At)
                    {
                        FuncContents *cg = f->at_func.get();
                        bool ok;
                        if (cg == sg)
                        {
                            // Same host: store var must be the same loop or an
                            // outer one (outer = higher dim index; arg0 = inner).
                            ok = dim_index(sg, sv) >= dim_index(cg, f->at_var);
                        }
                        else
                        {
                            // Different host: the compute host's loop (cg, at_var)
                            // must itself sit inside the store loop (sg, sv).
                            ok = enclosed_by(cg, sg, sv);
                        }
                        if (!ok)
                        {
                            fail(f, "store level does not enclose the compute level "
                                    "(store_at must be at the same or an outer loop)");
                        }
                    }
                    // root compute level cannot be enclosed by a non-root store
                    // level: store_root().compute_root() is the only equal case
                    // and is handled (no store node) elsewhere.
                    if (f->level == FuncContents::Level::Root)
                    {
                        fail(f, "store level is inside the compute level (compute_root) "
                                "(store_at must be at the same or an outer loop)");
                    }
                }
            }

            // -- Hoist-storage-level legality (loopdoc.md section 8) -----------
            if (f->has_hoist_level)
            {
                // hoist_storage/hoist_storage_root requires a non-inline compute
                // level, just like store_at.
                if (f->level == FuncContents::Level::Inline)
                {
                    fail(f, "has a hoist-storage level (hoist_storage/hoist_storage_root) "
                            "but is inlined; Funcs that use hoist_storage must also call "
                            "compute_at/compute_root");
                }
                if (!f->hoist_is_root)
                {
                    FuncContents *hg = f->hoist_func.get();
                    const std::string &hv = f->hoist_var;
                    if (!hg || !is_realized(hg))
                    {
                        fail(f, "hoist_storage host is inlined/undefined, so it has no loop");
                    }
                    if (dim_index(hg, hv) < 0)
                    {
                        fail(f, "hoist_storage loop variable does not exist in the host Func");
                    }
                    // The hoist-storage level must ENCLOSE the store level, which
                    // in turn encloses the compute level. The effective store
                    // level is the explicit store_at if present, else the compute
                    // level. We require the hoist loop to be the same loop or an
                    // outer one relative to that effective store level.
                    bool store_is_root = f->has_store_level && f->store_is_root;
                    FuncContents *eff_host;
                    std::string eff_var;
                    if (f->has_store_level && !f->store_is_root)
                    {
                        eff_host = f->store_func.get();
                        eff_var = f->store_var;
                    }
                    else if (f->level == FuncContents::Level::At)
                    {
                        eff_host = f->at_func.get();
                        eff_var = f->at_var;
                    }
                    else
                    {
                        // compute_root with no explicit store_at: effective store
                        // level is root, which only a root hoist level encloses.
                        eff_host = nullptr;
                        eff_var.clear();
                        store_is_root = true;
                    }

                    bool ok;
                    if (store_is_root)
                    {
                        // A root store level can only be enclosed by a root hoist
                        // level (handled above); a named hoist loop is inside it.
                        ok = false;
                    }
                    else if (eff_host == hg)
                    {
                        // Same host: hoist var must be the same loop or an outer
                        // one (outer = higher dim index; arg0 = inner).
                        ok = dim_index(hg, hv) >= dim_index(eff_host, eff_var);
                    }
                    else
                    {
                        // Different host: the store host's loop (eff_host, eff_var)
                        // must itself sit inside the hoist loop (hg, hv).
                        ok = enclosed_by(eff_host, hg, hv);
                    }
                    if (!ok)
                    {
                        fail(f, "hoist-storage level does not enclose the store level "
                                "(hoist_storage must be at the same or an outer loop)");
                    }
                }
            }

            if (f->level != FuncContents::Level::At)
            {
                continue;
            }
            FuncContents *g = f->at_func.get();
            const std::string &v = f->at_var;

            // The host must itself be computed (have a loop nest).
            if (!g || !is_realized(g))
            {
                fail(f, "compute_at host is inlined/undefined, so it has no loop to compute at");
            }
            // The named loop must exist as a dimension of SOME stage of the host
            // (loopdoc.md section 10: an RVar loop lives only in its own stage,
            // but is still a valid compute_at site).
            int gs = resolve_stage(g, v);
            if (gs < 0)
            {
                fail(f, "compute_at loop variable does not exist in the host Func");
            }
            // Every use of f -- across EVERY stage of every realized reader
            // (loopdoc.md section 10) -- must be enclosed by the site (g, gs, v);
            // otherwise some reader cannot see f's values. (A use outside the
            // site is the classic producer/consumer break that requires a
            // wrapper Func to fix.)
            for (FuncContents *h : funcs)
            {
                if (h == f || !is_realized(h))
                {
                    continue;
                }
                for (int hs = 0; hs < num_stages(h); hs++)
                {
                    if (stage_reads(h, hs, f) && !site_encloses_use(g, gs, v, h, hs))
                    {
                        fail(f, "it is read by a Func (or update stage) that is not "
                                "computed inside the compute_at loop (the "
                                "producer/consumer relationship is broken)");
                    }
                }
            }
        }
    }

    // Does this Func have a store level that must be drawn as a `store` node?
    // True only when a store level was explicitly set AND it differs from the
    // compute level (store_root().compute_root() => equal => no node).
    static bool has_store_node(FuncContents *f)
    {
        if (!f->has_store_level)
        {
            return false;
        }
        if (f->store_is_root)
        {
            // Differs from compute level unless compute is also root.
            return f->level != FuncContents::Level::Root;
        }
        // store_at(g, v): equal to compute level iff same host and same var.
        if (f->level == FuncContents::Level::At && f->at_func.get() == f->store_func.get() &&
            f->at_var == f->store_var)
        {
            return false;
        }
        return true;
    }

    // Emit the realizations in `funcs` as a chain of produce/consume blocks.
    // `cont` (if non-null) is the continuation that follows the last func and
    // is wrapped by its consume block.
    template <typename Cont>
    void emit_realizations(const std::vector<FuncContents *> &funcs, size_t i,
                           int indent, const Cont &cont, bool has_cont)
    {
        if (i >= funcs.size())
        {
            if (has_cont)
            {
                cont(indent);
            }
            return;
        }
        FuncContents *f = funcs[i];
        out << pad(indent) << "produce " << f->name << ":\n";
        emit_func_loops(f, indent + 2);

        bool more = (i + 1 < funcs.size()) || has_cont;
        if (more)
        {
            out << pad(indent) << "consume " << f->name << ":\n";
            emit_realizations(funcs, i + 1, indent + 2, cont, has_cont);
        }
    }

    // Emit f's loop nests. A Func with update definitions (loopdoc.md section
    // 10) emits ONE loop nest per stage, in stage order, all inside the single
    // `produce f` -- no `consume` between stages. Each stage walks its own
    // dimension list (dim0 innermost), injecting any compute_at children filed
    // at that (f, stage, var) level, with that stage's leaf at the center.
    void emit_func_loops(FuncContents *f, int indent)
    {
        for (int s = 0; s < num_stages(f); s++)
        {
            emit_dim(f, s, (int)stage_dims(f, s).size() - 1, indent);
        }
    }

    void emit_dim(FuncContents *f, int stage, int dim, int indent)
    {
        if (dim < 0)
        {
            // A loop-less stage (no dims) is the innermost point: inject any
            // non-pure-inline children filed here (sentinel empty var name, see
            // print()), wrapping the leaf in their consume (loopdoc.md sec 11).
            SiteKey leaf_key{f, stage, std::string()};
            auto lit = children_at.find(leaf_key);
            if (lit != children_at.end() && !lit->second.empty())
            {
                auto leaf = [this, f](int ind) {
                    out << pad(ind) << f->name << "(...) = ...\n";
                };
                emit_realizations(lit->second, 0, indent, leaf, /*has_cont=*/true);
                return;
            }
            out << pad(indent) << f->name << "(...) = ...\n";
            return;
        }
        const std::string &var = stage_dims(f, stage)[dim].name();

        // An elided ("collapsed") loop prints no `for` line and does not
        // indent its body, but is still a valid injection site for any
        // compute_at children filed at this level (see the "compute_at at an
        // elided loop level" example).
        bool elided = stage_collapsed(f, stage).count(var) != 0;
        int body_indent = indent;
        if (!elided)
        {
            out << pad(indent) << "for " << var << ":\n";
            body_indent = indent + 2;
        }

        SiteKey key{f, stage, var};
        auto it = children_at.find(key);
        std::vector<FuncContents *> empty;
        const std::vector<FuncContents *> &kids = (it == children_at.end()) ? empty : it->second;

        auto deeper = [this, f, stage, dim](int ind) { emit_dim(f, stage, dim - 1, ind); };
        auto inject = [this, &kids, &deeper](int ind) {
            emit_realizations(kids, 0, ind, deeper, /*has_cont=*/true);
        };

        // Open any `store h:` nodes filed at this loop level, wrapping the
        // child injection and the deeper loops. (loopdoc.md section 8.)
        auto sit = store_at_level.find(key);
        if (sit != store_at_level.end())
        {
            emit_store_nodes(sit->second, body_indent, inject);
        }
        else
        {
            inject(body_indent);
        }
    }

    // Emit a nested stack of `store h:` lines at `indent`, then run `body` at
    // the innermost (deepest) indent inside all of them.
    template <typename Body>
    void emit_store_nodes(const std::vector<FuncContents *> &nodes, int indent, const Body &body)
    {
        if (nodes.empty())
        {
            body(indent);
            return;
        }
        for (FuncContents *h : nodes)
        {
            out << pad(indent) << "store " << h->name << ":\n";
            indent += 2;
        }
        body(indent);
    }

    void print(FuncContents *output)
    {
        // The output Func is always computed at the outermost (root) level.
        output->level = FuncContents::Level::Root;
        output->at_func.reset();

        // Establish first-visitation order (tie-breaker), then realization order.
        std::set<FuncContents *> visit_seen;
        std::uint64_t counter = 0;
        compute_visit_order(output, visit_seen, counter);

        std::set<FuncContents *> visited;
        std::vector<FuncContents *> order;
        realization_order(output, visited, order);

        // Reject illegal schedules before emitting (mirrors Halide aborting).
        validate(order);

        std::vector<FuncContents *> root_list;
        for (FuncContents *f : order)
        {
            if (f->level == FuncContents::Level::Root)
            {
                root_list.push_back(f);
            }
            else if (f->level == FuncContents::Level::At)
            {
                // (host, var) denotes the `var` loop in EVERY stage of the host
                // (loopdoc.md section 7): f is injected just inside that loop in
                // each stage of the host whose body at that level USES f, and
                // stages that do not use f get nothing. "Uses" is transitive: the
                // body uses f directly (the stage reads f) OR through another
                // producer realized in that body that itself uses f (loopdoc.md
                // section 7, "Computing at an indirect consumer's loop"). So a
                // producer read by several stages lands once per using stage, and
                // an indirect producer lands wherever its consumer chain is
                // realized. (For an RVar site only one stage has the loop, so this
                // reduces to a single injection.)
                FuncContents *host = f->at_func.get();
                for (int hs = 0; hs < num_stages(host); hs++)
                {
                    if (stage_dim_index(host, hs, f->at_var) >= 0 &&
                        body_uses(host, hs, f->at_var, f, order))
                    {
                        children_at[{host, hs, f->at_var}].push_back(f);
                    }
                }
            }
            else if (f->level == FuncContents::Level::Inline && !is_pure(f))
            {
                // A non-pure Func left at the inline level cannot be textually
                // substituted (a reduction is not an expression), so the inline
                // level REALIZES it -- at the innermost point of EACH use,
                // independently per use (loopdoc.md section 11). For every
                // realized Func h and every stage hs of h that reads f, file f
                // at the innermost loop of that stage (dims[0]); the leaf of h's
                // stage then sits inside f's `consume`. (The injection-site
                // machinery treats a collapsed innermost loop as a valid site,
                // matching "as close to the innermost loop as possible".)
                for (FuncContents *h : order)
                {
                    if (h == f || !is_realized(h))
                    {
                        continue;
                    }
                    for (int hs = 0; hs < num_stages(h); hs++)
                    {
                        if (!stage_reads(h, hs, f))
                        {
                            continue;
                        }
                        const std::vector<DimData> &d = stage_dims(h, hs);
                        if (d.empty())
                        {
                            // No loops in this stage: file f directly at the
                            // produce level (innermost == the leaf level). We
                            // model this with an empty var name as a sentinel
                            // injection site at this stage.
                            children_at[{h, hs, std::string()}].push_back(f);
                        }
                        else
                        {
                            children_at[{h, hs, d[0].name()}].push_back(f);
                        }
                    }
                }
            }
            // Pure inline funcs are non-realized and never appear.

            // File this Func's `store` node, if any, at its store level.
            if (has_store_node(f))
            {
                if (f->store_is_root)
                {
                    store_root_funcs.push_back(f);
                }
                else
                {
                    FuncContents *sh = f->store_func.get();
                    int ss = resolve_stage(sh, f->store_var);
                    store_at_level[{sh, ss, f->store_var}].push_back(f);
                }
            }
        }

        auto no_cont = [](int) {};
        auto chain = [this, &root_list, &no_cont](int ind) {
            emit_realizations(root_list, 0, ind, no_cont, /*has_cont=*/false);
        };
        // store_root() nodes wrap the entire top-level chain.
        emit_store_nodes(store_root_funcs, 0, chain);
    }
};

} // namespace internal

inline void Func::print_loop_nest()
{
    internal::LoopNestPrinter printer(std::cerr);
    printer.print(contents.get());
}

} // namespace micro_halide
