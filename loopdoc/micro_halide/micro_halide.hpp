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

// ---------------------------------------------------------------------------
// FuncContents: the shared, mutable state behind a Func handle (mirrors
// Halide::Internal::Function). Copying a Func shares this state.
// ---------------------------------------------------------------------------
struct FuncContents
{
    std::string name;

    // Pure dimensions, in definition order. args[0] is the innermost loop.
    std::vector<Var> args;
    bool defined = false;

    // Funcs directly read by this Func's definition (deduplicated).
    std::vector<std::shared_ptr<FuncContents>> producers;

    // Where this Func is computed in the loop nest.
    enum class Level
    {
        Inline, // default: substituted into its consumers, no loops of its own
        Root,   // computed once at the outermost level
        At      // computed inside at_func's loop over at_var
    };
    Level level = Level::Inline;

    std::shared_ptr<FuncContents> at_func; // host, for Level::At
    std::string at_var;                    // host loop var name, for Level::At

    // Store level (where this Func's buffer is allocated), set by store_at /
    // store_root. Defaults to "same as the compute level" (has_store_level ==
    // false), which prints no `store` node.
    //
    // NOTE TO MICRO-AGENT: the emission of the `store` node and the legality of
    // a store level are NOT yet implemented -- they are your task, per
    // loopdoc.md section 8. The fields below just record what was requested.
    bool has_store_level = false;          // a store level was explicitly set
    bool store_is_root = false;            // store_root() (else store_at)
    std::shared_ptr<FuncContents> store_func; // host, for store_at
    std::string store_var;                 // host loop var name, for store_at

    // Hoist-storage level (where the physical allocation is placed), set by
    // hoist_storage / hoist_storage_root. Has NO effect on print_loop_nest
    // output (see loopdoc.md section 8); it only adds legality constraints.
    //
    // NOTE TO MICRO-AGENT: the legality of a hoist-storage level is NOT yet
    // implemented -- it is your task, per loopdoc.md section 8. The fields below
    // just record what was requested; the printer ignores them.
    bool has_hoist_level = false;          // a hoist-storage level was set
    bool hoist_is_root = false;            // hoist_storage_root() (else hoist_storage)
    std::shared_ptr<FuncContents> hoist_func; // host, for hoist_storage
    std::string hoist_var;                 // host loop var name, for hoist_storage

    // Names of this Func's loop variables that Halide elides because their
    // required extent is provably 1 (a "point loop"). See `micro_halide_collapses`
    // below and loopdoc.md: this is *declared* per example (it depends on bounds
    // inference, which is out of scope), not derived. An elided loop drops its
    // `for` line but is still a valid injection site for compute_at children.
    // (For a Func with updates this collapse-set is shared by all stages; the
    // current examples only need per-Func collapsing.)
    std::set<std::string> collapsed;

    // ---- Update (reduction) definitions (loopdoc.md section 10) ----------
    //
    // The fields above describe stage 0 (the pure/initial definition): `args`
    // is its dimension list and `producers` its reads. A Func may also have
    // UPDATE stages, captured here in definition order (updates[0] == s1, ...).
    //
    // The main agent has CAPTURED the raw per-stage data that only the C++
    // types can distinguish -- which LHS args are plain (free) Vars vs. general
    // expressions, and which RVars the stage references. Turning that into each
    // stage's DIMENSION LIST (free Vars + RVars, in the documented order) and
    // EMITTING the multiple stages inside one `produce` (plus per-stage
    // scheduling, RVar sites, and the cross-stage legal-site rule) is the
    // micro-agent's task, from loopdoc.md section 10. `dims` is intentionally
    // left for you to populate.
    struct Update
    {
        std::vector<Var> pure_args;  // bare-Var LHS args (free dims), in order
        std::vector<std::string> rvars; // distinct RVar names used, in order
        std::vector<std::shared_ptr<FuncContents>> producers; // funcs this stage reads
        std::vector<Var> dims;       // this stage's loop list (micro-agent fills)
        std::set<std::string> collapsed; // per-stage point-loop elision
    };
    std::vector<Update> updates;
};

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
        FuncContents::Update u;
        for (size_t i = 0; i < vars.size(); i++)
        {
            if (is_var[i])
            {
                u.pure_args.push_back(vars[i]); // a bare Var LHS arg = free dim
            }
        }
        u.rvars = collect_rvars(rhs);
        u.producers = collect_producers(rhs);
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
        func->updates.push_back(std::move(u));
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
        func->args = vars;
        func->producers = collect_producers(rhs);
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

// ---------------------------------------------------------------------------
// Func: a handle to a (shared) FuncContents.
// ---------------------------------------------------------------------------
class Func
{
  public:
    std::shared_ptr<FuncContents> contents;

    Func() : contents(std::make_shared<FuncContents>())
    {
    }

    explicit Func(std::string name) : contents(std::make_shared<FuncContents>())
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

    // ---- Loop transforms (loopdoc.md section 6) --------------------------
    // These rewrite this Func's ordered dimension list (contents->args, with
    // args[0] the INNERMOST loop). They change only this Func's own loops and
    // the dimension names usable as compute_at/store_at sites; they never move
    // the Func relative to others and never change which values are computed.

    // Find a Var's index in the dimension list, or -1.
    int dim_pos(const std::string &name) const
    {
        for (int i = 0; i < (int)contents->args.size(); i++)
        {
            if (contents->args[i].name() == name)
            {
                return i;
            }
        }
        return -1;
    }

    // split(old, outer, inner, factor): replace `old` with two dimensions --
    // `inner` (innermost, at old's former slot) and `outer` just outside it.
    // [x, y] under split(x, xo, xi, 8) -> [xi, xo, y]. One extra `for`.
    Func &split(const Var &old_var, const Var &outer, const Var &inner, int factor)
    {
        (void)factor; // bound is normalized away by the harness
        int pos = dim_pos(old_var.name());
        if (pos < 0)
        {
            throw std::runtime_error("micro_halide: split: Func \"" + contents->name +
                                     "\" has no dimension \"" + old_var.name() + "\"");
        }
        // Replace args[pos] (== old) with [inner, outer]: inner takes old's slot
        // (innermost of the pair), outer sits just outside it.
        std::vector<Var> &a = contents->args;
        a.erase(a.begin() + pos);
        a.insert(a.begin() + pos, outer);   // outer goes to old's slot first ...
        a.insert(a.begin() + pos, inner);   // ... then inner pushed inside it
        return *this;
    }

    // fuse(inner, outer, fused): remove `inner` and `outer`, place a single
    // `fused` dimension at inner's former position. [x, y] under fuse(x, y, xy)
    // -> [xy]. One fewer `for`.
    Func &fuse(const Var &inner, const Var &outer, const Var &fused)
    {
        int ipos = dim_pos(inner.name());
        int opos = dim_pos(outer.name());
        if (ipos < 0)
        {
            throw std::runtime_error("micro_halide: fuse: Func \"" + contents->name +
                                     "\" has no dimension \"" + inner.name() + "\"");
        }
        if (opos < 0)
        {
            throw std::runtime_error("micro_halide: fuse: Func \"" + contents->name +
                                     "\" has no dimension \"" + outer.name() + "\"");
        }
        std::vector<Var> &a = contents->args;
        // Remove both, then insert `fused` at inner's (former) position. Erase
        // the higher index first so the lower index stays valid.
        int hi = std::max(ipos, opos);
        int lo = std::min(ipos, opos);
        a.erase(a.begin() + hi);
        a.erase(a.begin() + lo);
        // inner's former position, after removing the elements: if outer was
        // before inner (opos < ipos), inner shifts down by one.
        int insert_pos = (opos < ipos) ? ipos - 1 : ipos;
        a.insert(a.begin() + insert_pos, fused);
        return *this;
    }

    // tile(x, y, xo, yo, xi, yi, xf, yf): split(x,xo,xi,xf); split(y,yo,yi,yf);
    // reorder(xi, yi, xo, yo). [x, y] -> [xi, yi, xo, yo]. Two extra `for`s.
    Func &tile(const Var &x, const Var &y,
               const Var &xo, const Var &yo,
               const Var &xi, const Var &yi,
               int xfactor, int yfactor)
    {
        split(x, xo, xi, xfactor);
        split(y, yo, yi, yfactor);
        reorder(xi, yi, xo, yo);
        return *this;
    }

    // reorder(v_inner, ..., v_outer): lists dimensions innermost first and
    // permutes ONLY the listed dimensions among the slots they currently
    // occupy; unnamed dimensions keep their position. Each listed dimension
    // must exist and be named at most once.
    template <typename... Vars>
    Func &reorder(const Vars &...vars)
    {
        std::vector<std::string> names{vars.name()...};

        // Collect the slots occupied by the listed dimensions, in ascending
        // index order (innermost first). Validate existence and uniqueness.
        std::vector<int> slots;
        for (const std::string &n : names)
        {
            int pos = dim_pos(n);
            if (pos < 0)
            {
                throw std::runtime_error("micro_halide: reorder: Func \"" + contents->name +
                                         "\" has no dimension \"" + n + "\"");
            }
            for (int s : slots)
            {
                if (contents->args[s].name() == n)
                {
                    throw std::runtime_error("micro_halide: reorder: dimension \"" + n +
                                             "\" named more than once");
                }
            }
            slots.push_back(pos);
        }
        std::sort(slots.begin(), slots.end());

        // The listed dimensions, in the requested order (innermost first).
        std::vector<Var> requested;
        for (const std::string &n : names)
        {
            requested.push_back(contents->args[dim_pos(n)]);
        }

        // Drop the requested dimensions into the (sorted) slots they occupied.
        for (size_t i = 0; i < slots.size(); i++)
        {
            contents->args[slots[i]] = requested[i];
        }
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
class Stage
{
  public:
    std::shared_ptr<FuncContents> func;
    int index; // update index: updates[index]

    Stage(std::shared_ptr<FuncContents> f, int i) : func(std::move(f)), index(i)
    {
    }

    Stage &split(const Var &old_var, const Var &outer, const Var &inner, int factor)
    {
        (void)old_var; (void)outer; (void)inner; (void)factor;
        return *this; // TODO(micro-agent): per-stage split (loopdoc section 10)
    }
    Stage &fuse(const Var &inner, const Var &outer, const Var &fused)
    {
        (void)inner; (void)outer; (void)fused;
        return *this; // TODO(micro-agent): per-stage fuse
    }
    Stage &tile(const Var &x, const Var &y,
                const Var &xo, const Var &yo,
                const Var &xi, const Var &yi,
                int xfactor, int yfactor)
    {
        (void)x; (void)y; (void)xo; (void)yo; (void)xi; (void)yi;
        (void)xfactor; (void)yfactor;
        return *this; // TODO(micro-agent): per-stage tile
    }
    // reorder accepts Vars, RVars, or a 1-D RDom (anything with .name()).
    template <typename... Vars>
    Stage &reorder(const Vars &...vars)
    {
        (void)std::initializer_list<int>{(static_cast<void>(vars.name()), 0)...};
        return *this; // TODO(micro-agent): per-stage reorder (incl. RVars)
    }
};

inline Stage Func::update(int i) const
{
    return Stage(contents, i);
}

// ---------------------------------------------------------------------------
// micro_halide_collapses(f, {vars...}): declare that f's loops over the named
// Vars are elided by Halide (their required extent is 1). This is an annotation
// of ground truth supplied by the example author, NOT something micro_halide
// derives -- predicting it requires bounds inference, which loopdoc.md keeps
// out of scope. Under real Halide this is a no-op stub (see the
// halide_compat header); only the loop *structure* is what the docs teach and
// what micro_halide validates.
// ---------------------------------------------------------------------------
inline void micro_halide_collapses(const Func &f, std::initializer_list<Var> vars)
{
    for (const Var &v : vars)
    {
        f.contents->collapsed.insert(v.name());
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
    for (int i = 0; i < im.dimensions(); i++)
    {
        f.contents->args.push_back(Var("_" + std::to_string(i)));
    }
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

    // funcs computed_at a given (host, var-name) loop level, in realization order.
    std::map<std::pair<FuncContents *, std::string>, std::vector<FuncContents *>> children_at;

    // funcs whose `store` node opens at a given (host, var-name) loop level
    // (store_at with a store level outer to the compute level), in realization
    // order. A `store f:` node here wraps everything emitted deeper at that
    // level -- the host loops between the store and compute levels, and f's own
    // produce/consume at its compute level.
    std::map<std::pair<FuncContents *, std::string>, std::vector<FuncContents *>> store_at_level;

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

    static bool is_realized(FuncContents *f)
    {
        return f->level != FuncContents::Level::Inline;
    }

    // Index of a Var name among a Func's pure dims (arg0 = innermost), or -1.
    static int dim_index(FuncContents *g, const std::string &var)
    {
        for (int i = 0; i < (int)g->args.size(); i++)
        {
            if (g->args[i].name() == var)
            {
                return i;
            }
        }
        return -1;
    }

    // Does realized Func h's loop body reference f? f is referenced if it is a
    // direct producer of h, or reachable from h through a chain of *inlined*
    // producers (which are substituted into h). A realized intermediate stops
    // the search: h then reads that intermediate, not f.
    bool body_reads(FuncContents *h, FuncContents *f, std::set<FuncContents *> &seen)
    {
        if (!seen.insert(h).second)
        {
            return false;
        }
        for (auto &p : h->producers)
        {
            if (p.get() == f)
            {
                return true;
            }
            if (p->level == FuncContents::Level::Inline && body_reads(p.get(), f, seen))
            {
                return true;
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
            // The named loop must exist as a dimension of the host.
            if (dim_index(g, v) < 0)
            {
                fail(f, "compute_at loop variable does not exist in the host Func");
            }
            // Every consumer of f must be computed inside (g, v); otherwise some
            // reader cannot see f's values. (A consumer outside g is the classic
            // producer/consumer break that requires a wrapper Func to fix.)
            for (FuncContents *h : funcs)
            {
                if (h == f || !is_realized(h))
                {
                    continue;
                }
                std::set<FuncContents *> seen;
                if (body_reads(h, f, seen) && !enclosed_by(h, g, v))
                {
                    fail(f, "it is read by a Func that is not computed inside the "
                            "compute_at loop (the producer/consumer relationship is broken)");
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

    // Emit f's own loops (over its pure dims, args[0] innermost), injecting
    // any compute_at children at the appropriate loop level, with f's
    // definition at the center.
    void emit_func_loops(FuncContents *f, int indent)
    {
        emit_dim(f, (int)f->args.size() - 1, indent);
    }

    void emit_dim(FuncContents *f, int dim, int indent)
    {
        if (dim < 0)
        {
            out << pad(indent) << f->name << "(...) = ...\n";
            return;
        }
        const std::string &var = f->args[dim].name();

        // An elided ("collapsed") loop prints no `for` line and does not
        // indent its body, but is still a valid injection site for any
        // compute_at children filed at this level (see the "compute_at at an
        // elided loop level" example).
        bool elided = f->collapsed.count(var) != 0;
        int body_indent = indent;
        if (!elided)
        {
            out << pad(indent) << "for " << var << ":\n";
            body_indent = indent + 2;
        }

        auto it = children_at.find({f, var});
        std::vector<FuncContents *> empty;
        const std::vector<FuncContents *> &kids = (it == children_at.end()) ? empty : it->second;

        auto deeper = [this, f, dim](int ind) { emit_dim(f, dim - 1, ind); };
        auto inject = [this, &kids, &deeper](int ind) {
            emit_realizations(kids, 0, ind, deeper, /*has_cont=*/true);
        };

        // Open any `store h:` nodes filed at this loop level, wrapping the
        // child injection and the deeper loops. (loopdoc.md section 8.)
        auto sit = store_at_level.find({f, var});
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
                children_at[{f->at_func.get(), f->at_var}].push_back(f);
            }
            // Inline funcs are not realized and never appear.

            // File this Func's `store` node, if any, at its store level.
            if (has_store_node(f))
            {
                if (f->store_is_root)
                {
                    store_root_funcs.push_back(f);
                }
                else
                {
                    store_at_level[{f->store_func.get(), f->store_var}].push_back(f);
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
