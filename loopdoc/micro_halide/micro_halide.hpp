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
#include <functional>
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

// Added by human to support handwritten harness rule change.
// Use these to report all exceptions.
class CompileError: public std::runtime_error
{
  public:
    CompileError(std::string what) : std::runtime_error(std::move(what))
    {
    }
};
class InternalError: public std::runtime_error
{
  public:
    InternalError(std::string what) : std::runtime_error(std::move(what))
    {
    }
};

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

// Param<T>: a scalar runtime parameter. Its VALUE is irrelevant to the loop
// nest; it exists only to form specialize() conditions (loopdoc.md section 15).
// Distinct Param objects are distinct conditions, and examples never reuse a
// condition Expr, so micro_halide need not dedup specializations by condition
// (loopdoc.md section 15 "Out of scope").
template <typename T>
class Param
{
  public:
    Param()
    {
    }
    explicit Param(const std::string &)
    {
    }
    operator Expr() const
    {
        return Expr();
    }
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


class VarOrRVar
{
    std::string _name;

  public:
    // Public because we need to be source compatible with Halide.
    // Yes, it's `v.name()`, but `v.is_rvar` alone without `()`...
    bool is_rvar;

    VarOrRVar(const Var &v): _name(v.name()), is_rvar(false)
    {
    }
    VarOrRVar(const RVar &v): _name(v.name()), is_rvar(true)
    {
    }
    VarOrRVar(const RDom &v): _name(v.name()), is_rvar(true)
    {
    }

    const std::string &name() const
    {
        return _name;
    }
};


struct DimData
{
    std::string _name;
    bool _is_rvar;

    DimData(std::string n, bool is_rvar) : _name(std::move(n)), _is_rvar(is_rvar)
    {
    }

    const std::string &name() const
    {
        return _name;
    }

    bool is_rvar() const
    {
        return _is_rvar;
    }

    DimData with_name(std::string new_name) const
    {
        DimData _new = *this;
        _new._name = std::move(new_name);  // So old _new._name is wasted, but this is just a quick experiment.
        return _new;
    }

    template <typename VarList>
    static std::vector<DimData> from_pure_var_list(const VarList& lst)
    {
        std::vector<DimData> result;
        result.reserve(lst.size());
        for (const auto& var : lst) {
            result.emplace_back(var.name(), false);
        }
        return result;
    }
};

// ---------------------------------------------------------------------------
// FuncContents: the shared, mutable state behind a Func handle (mirrors
// Halide::Internal::Function). Copying a Func shares this state.
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Specialization (loopdoc.md section 15): one entry of a stage's ordered
// specialization list -- a run-time condition paired with ITS OWN forked copy
// of the stage's schedule (a full StageData, itself able to carry further
// specializations, so the branches form a tree). specialize() appends one of
// these and returns a handle to `schedule`. The condition is only carried for
// fidelity to the API; print_loop_nest() never prints it (loopdoc.md section
// 15: the printer walks into every branch with no if/else marker).
// ---------------------------------------------------------------------------
struct StageData;
struct Specialization
{
    Expr condition;
    std::shared_ptr<StageData> schedule;  // forked copy of the schedule so far
};

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

    // compute_with (loopdoc.md section 14): if this stage has been fused into a
    // PARENT stage, this records the fuse edge -- the parent Func, the parent's
    // stage index, and the shared loop level (the `v` named in compute_with).
    // The state is per stage; calling compute_with again on the same stage
    // OVERWRITES this (loopdoc.md section 14). has_fuse == false means this is an
    // *unfused* stage (it starts its own loop nest).
    bool has_fuse = false;
    std::shared_ptr<FuncContents> fuse_parent;  // the argument stage's Func
    int fuse_parent_stage = 0;                  // the argument stage index
    std::string fuse_var;                       // shared loop level name `v`

    // specialize (loopdoc.md section 15): this stage's ordered list of
    // conditional schedule variants. Empty by default. Each entry pairs a
    // condition with a forked copy of the schedule (see Specialization); the
    // list lowers to `if c0 {branch0} else if c1 {branch1} ... else {fallback}`
    // in declaration order, and the printer emits one loop nest per branch
    // (branches first, this stage's own dims as the fallback last), all inside
    // the single `produce`. Calling specialize() again on the same handle
    // appends a sibling here; calling it on a returned branch handle descends
    // into that branch's own list (nesting), so the branches form a tree.
    std::vector<Specialization> specializations;

    // specialize_fail (loopdoc.md section 15): terminates the chain -- the final
    // `else` is a run-time assertion carrying no loops, so the fallback nest
    // (this stage's own dims) is NOT emitted, only the specialization branches.
    bool specialize_failed = false;
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

    // NOTE: the Funcs read by this Func are NOT cached here. Per-stage reads live
    // in each StageData::producers (the single source of truth); the whole-Func
    // producer set is computed on demand by all_producers() below. This avoids
    // the staleness bugs a cached union invites -- e.g. rfactor rewriting a
    // stage's reads while a cached union kept pointing at the old callee.

    // Where this Func is computed in the loop nest (whole-Func: all stages).
    enum class Level
    {
        Inline, // default: substituted into its consumers, no loops of its own
        Root,   // computed once at the outermost level
        At      // computed inside at_func's loop over at_var
    };
    Level level = Level::Inline;

    std::shared_ptr<FuncContents> at_func; // site_func, for Level::At
    std::string at_var;                    // site_func loop var name, for Level::At

    // Store level (where this Func's buffer is allocated; whole-Func), set by
    // store_at / store_root. Defaults to "same as the compute level"
    // (has_store_level == false), which prints no `store` node.
    bool has_store_level = false;          // a store level was explicitly set
    bool store_is_root = false;            // store_root() (else store_at)
    std::shared_ptr<FuncContents> store_func; // site_func, for store_at
    std::string store_var;                 // site_func loop var name, for store_at

    // Hoist-storage level (where the physical allocation is placed; whole-Func),
    // set by hoist_storage / hoist_storage_root. Has NO effect on
    // print_loop_nest output (loopdoc.md section 8); only legality.
    bool has_hoist_level = false;          // a hoist-storage level was set
    bool hoist_is_root = false;            // hoist_storage_root() (else hoist_storage)
    std::shared_ptr<FuncContents> hoist_func; // site_func, for hoist_storage
    std::string hoist_var;                 // site_func loop var name, for hoist_storage

    // in() / clone_in() wrappers (loopdoc.md section 13). The wrapper/clone is
    // recorded on the WRAPPED Func (this one), keyed by the redirected
    // *direct-caller* consumer (transitive-caller normalization is resolved at
    // in()/clone_in() time -- see Func::in/clone_in -- so the keys here are the
    // direct callers of this Func that should read the wrapper instead). The
    // consumers' reads are not mutated; the producer-accessor seam
    // (func_producers/stage_producers) performs the substitution as a one-time
    // pass at the start of nest construction. A `global_wrapper` (from `f.in()`)
    // redirects every consumer that has no per-consumer wrapper of its own.
    std::map<FuncContents *, std::shared_ptr<FuncContents>> wrappers;
    std::shared_ptr<FuncContents> global_wrapper;
};

// ---------------------------------------------------------------------------
// The whole-Func producer set, computed on demand from the stages (there is no
// cached copy). It is the union, in first-appearance order and deduplicated, of
// every stage's direct reads AND every specialization branch's reads (a producer
// read only inside a `specialize` branch -- e.g. an rfactor intermediate built
// on a branch -- is still a producer of the Func). Reading from the stages means
// a later rewrite of a stage (rfactor moving a read into an intermediate) is
// reflected automatically, with no stale edge to prune.
// ---------------------------------------------------------------------------
inline void gather_stage_tree_producers(const StageData &st,
                                        std::vector<std::shared_ptr<FuncContents>> &out)
{
    for (const auto &p : st.producers)
    {
        out.push_back(p);
    }
    for (const Specialization &sp : st.specializations)
    {
        gather_stage_tree_producers(*sp.schedule, out);
    }
}

inline std::vector<std::shared_ptr<FuncContents>> all_producers(const FuncContents *f)
{
    std::vector<std::shared_ptr<FuncContents>> raw;
    for (const StageData &st : f->stages)
    {
        gather_stage_tree_producers(st, raw);
    }
    std::vector<std::shared_ptr<FuncContents>> out;
    std::set<FuncContents *> seen;
    for (auto &p : raw)
    {
        if (seen.insert(p.get()).second)
        {
            out.push_back(p);
        }
    }
    return out;
}

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

inline void require_rvar_match(const VarOrRVar &old, const VarOrRVar &_new, const char* verb)
{
    if (old.is_rvar != _new.is_rvar) {
        auto _format = [] (const VarOrRVar &v)
        {
            return v.name() + ":" + (v.is_rvar ? "RVar" : "Var");
        };
        throw CompileError(
            "micro_halide::dimlist::require_rvar_match: cannot "
            + std::string(verb) + " " + _format(old) + " into " + _format(_new));
    }
}

inline void split(std::vector<DimData> &a, const std::string &owner,
                  const VarOrRVar &old_var, const VarOrRVar &outer, const VarOrRVar &inner)
{
    require_rvar_match(old_var, outer, "split");
    require_rvar_match(old_var, inner, "split");
    int pos = dim_pos(a, old_var.name());
    if (pos < 0)
    {
        throw CompileError("micro_halide: split: \"" + owner +
                           "\" has no dimension \"" + old_var.name() + "\"");
    }
    const DimData old_dim = a[pos];
    a.erase(a.begin() + pos);
    a.insert(a.begin() + pos, old_dim.with_name(outer.name())); // outer goes to old's slot first ...
    a.insert(a.begin() + pos, old_dim.with_name(inner.name())); // ... then inner pushed inside it
}

inline void fuse(std::vector<DimData> &a, const std::string &owner,
                 const VarOrRVar &inner, const VarOrRVar &outer, const VarOrRVar &fused)
{
    require_rvar_match(inner, fused, "fuse");
    require_rvar_match(outer, fused, "fuse");
    int ipos = dim_pos(a, inner.name());
    int opos = dim_pos(a, outer.name());
    if (ipos < 0)
    {
        throw CompileError("micro_halide: fuse: \"" + owner +
                           "\" has no dimension \"" + inner.name() + "\"");
    }
    if (opos < 0)
    {
        throw CompileError("micro_halide: fuse: \"" + owner +
                           "\" has no dimension \"" + outer.name() + "\"");
    }
    // TODO do we inherit state from inner or outer position?
    // For is_rvar it doesn't matter since they'll match, but what about GPU etc.
    const DimData old_dim = a[ipos];
    // The fused dim is placed where the inner dim was. The outer dim is deleted.
    a[ipos] = old_dim.with_name(fused.name());
    a.erase(a.begin() + opos);
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
            throw CompileError("micro_halide: reorder: \"" + owner +
                               "\" has no dimension \"" + n + "\"");
        }
        for (int s : slots)
        {
            if (a[s].name() == n)
            {
                throw CompileError("micro_halide: reorder: dimension \"" + n +
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

inline bool is_rvar_name(const std::vector<DimData> &a, const std::string &name)
{
    for (const DimData &d : a)
    {
        if (name == d.name())
        {
            return d.is_rvar();
        }
    }
    throw InternalError("Internal micro_halide_error @ is_rvar_name " + name);
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

    // Record one update stage (loopdoc.md section 10): build its default
    // dimension list (RVars innermost, pure Vars outside) from the LHS/RHS.
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
            // true marks this dim as an RVar (reduction) loop
            u.dims.push_back(DimData(n, true));
        }
        for (const Var &v : pure_args)
        {
            u.dims.push_back(DimData(v.name(), false));
        }
        // This stage's reads live in u.producers; the whole-Func producer set is
        // derived from the stages on demand (all_producers), so nothing to cache.
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
        s0.dims = DimData::from_pure_var_list(vars);
        s0.producers = collect_producers(rhs);
        func->stages.push_back(std::move(s0));
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
//
// !!! PUT SHARED Func/Stage SCHEDULING METHODS HERE, NOT IN BOTH Func AND Stage.
// If a scheduling operator makes sense on both a Func (its pure stage) and a
// Stage (an update stage) -- which is MOST of them: split/fuse/reorder/tile,
// compute_with, specialize/specialize_fail, ... -- declare it ONCE on this CRTP
// base. It operates on whichever stage `stage_index` names, and both Func and
// Stage inherit it. Do NOT copy-paste a declaration into class Func and class
// Stage: that is duplicated code that drifts, and it is exactly the thing the
// maintainer keeps having to undo. (Methods that genuinely belong to only one --
// e.g. rfactor / in / clone_in on Func or update() on Func -- stay on that class.)
// If a method must return `Stage` (incomplete here), declare it in-class and
// define it out of line below, after Stage is complete, as a template method of
// FuncStageImpl<Derived>.
// ---------------------------------------------------------------------------
template <typename Derived>
class FuncStageImpl
{
  public:
    std::shared_ptr<FuncContents> contents;
    int stage_index;  // 0 for pure stage, 1 + n for n-th update stage.

    // specialize (loopdoc.md section 15): when non-null, this handle addresses a
    // specialization BRANCH's forked schedule (returned by specialize()), not a
    // base stage in `contents->stages`. Further scheduling on the handle then
    // affects the branch only; specialize() on it nests a child branch. `stage()`
    // hides the distinction: it returns the branch when set, else the base stage.
    std::shared_ptr<StageData> branch;

    FuncStageImpl(std::shared_ptr<FuncContents> _contents, int _stage_index)
      : contents(std::move(_contents))
      , stage_index(_stage_index)
    {
    }

    // The StageData this handle schedules: a specialization branch if this is a
    // branch handle (loopdoc.md section 15), otherwise the base stage
    // (stages[0] pure; update i -> stage i+1).
    StageData &stage()
    {
        return branch ? *branch : contents->stages[stage_index];
    }

    // split(old, outer, inner, factor): replace `old` with two dimensions --
    // `inner` (innermost, at old's former slot) and `outer` just outside it.
    // [x, y] under split(x, xo, xi, 8) -> [xi, xo, y]. One extra `for`.
    Derived &split(const VarOrRVar &old_var, const VarOrRVar &outer, const VarOrRVar &inner, int factor)
    {
        (void)factor; // bound is normalized away by the harness
        dimlist::split(dims(), owner(), old_var, outer, inner);
        return static_cast<Derived&>(*this);
    }

    // fuse(inner, outer, fused): remove `inner` and `outer`, place a single
    // `fused` dimension at inner's former position. [x, y] under fuse(x, y, xy)
    // -> [xy]. One fewer `for`.
    Derived &fuse(const VarOrRVar &inner, const VarOrRVar &outer, const VarOrRVar &fused)
    {
        dimlist::fuse(dims(), owner(), inner, outer, fused);
        return static_cast<Derived&>(*this);
    }

    // tile(x, y, xo, yo, xi, yi, xf, yf): split(x,xo,xi,xf); split(y,yo,yi,yf);
    // reorder(xi, yi, xo, yo). [x, y] -> [xi, yi, xo, yo]. Two extra `for`s.
    Derived &tile(const VarOrRVar &x, const VarOrRVar &y,
                  const VarOrRVar &xo, const VarOrRVar &yo,
                  const VarOrRVar &xi, const VarOrRVar &yi,
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

    // -----------------------------------------------------------------------
    // Loop types / ForType (loopdoc.md section 17): serial / parallel /
    // vectorized / unrolled and the GPU family. UNIMPLEMENTED STUBS -- a
    // micro-agent must implement these FROM loopdoc.md section 17 ALONE. Each
    // sets the loop TYPE (and, for gpu_*, a device) that print_loop_nest shows
    // as the leading token on the loop line (`parallel`/`vectorized`/... instead
    // of `for`) plus a `<device_api>` suffix for GPU loops. Section 17 also
    // governs: the factor forms imply a split and type ONE half (vectorize/
    // unroll -> the inner, parallel -> the outer); split/fuse/reorder carry the
    // type along with the dimension; and compute_with requires the paired
    // dimensions to share a type. These are shared Func/Stage methods per the
    // note above -- do not duplicate them onto Func and Stage.
    // -----------------------------------------------------------------------
    Derived &serial(const VarOrRVar &var) { (void)var; throw InternalError("todo"); }
    Derived &parallel(const VarOrRVar &var) { (void)var; throw InternalError("todo"); }
    Derived &parallel(const VarOrRVar &var, int factor) { (void)var; (void)factor; throw InternalError("todo"); }
    Derived &vectorize(const VarOrRVar &var) { (void)var; throw InternalError("todo"); }
    Derived &vectorize(const VarOrRVar &var, int factor) { (void)var; (void)factor; throw InternalError("todo"); }
    Derived &unroll(const VarOrRVar &var) { (void)var; throw InternalError("todo"); }
    Derived &unroll(const VarOrRVar &var, int factor) { (void)var; (void)factor; throw InternalError("todo"); }
    Derived &gpu_blocks(const VarOrRVar &bx) { (void)bx; throw InternalError("todo"); }
    Derived &gpu_threads(const VarOrRVar &tx) { (void)tx; throw InternalError("todo"); }
    Derived &gpu_tile(const VarOrRVar &x, const VarOrRVar &bx, const VarOrRVar &tx, int factor)
    {
        (void)x; (void)bx; (void)tx; (void)factor; throw InternalError("todo");
    }

    // compute_with (loopdoc.md section 14): record a per-stage fuse edge from
    // THIS (child) stage into `parent` at loop level `var`, sharing the loops
    // from the outermost down to `var`. Records state only; the fused nest is
    // built later (loopdoc.md sections 14 + 15), and re-calling on the same
    // stage overwrites the edge (loopdoc.md section 14). Works on a Func (its
    // pure stage) or a Stage (an update stage), with either kind as `parent`.
    // The fuse level may be a Var or an RVar -- it is kept as a loop name, the
    // same way the dimension list stores Vars and RVars (section 10).
    template <typename ParentDerived>
    Derived &compute_with(const FuncStageImpl<ParentDerived> &parent, const VarOrRVar &var)
    {
        StageData &s = stage();
        s.has_fuse = true;
        s.fuse_parent = parent.contents;
        s.fuse_parent_stage = parent.stage_index;
        s.fuse_var = var.name();
        return static_cast<Derived&>(*this);
    }

    // This update stage's dimension list (loopdoc.md section 10): the same
    // ordered list the printer walks, so transforming it here rewrites only
    // THIS stage's loops. RVars sit in the list just like Vars.
    std::vector<DimData> &dims()
    {
        return stage().dims; // base stage, or a specialization branch (section 15)
    }
    const std::string &owner() const
    {
        return contents->name;
    }

    void unscheduled()
    {
        // no-op for Halide compatibility.
        // Not in scope for micro_halide: replicating the rule:
        // > This counts as a schedule, so calling this twice on the same Stage will fail the assertion.
    }

    // specialize / specialize_fail (loopdoc.md section 15): give THIS stage's
    // definition a conditional variant. specialize() forks a COPY of the schedule
    // so far and returns a handle to it (so further scheduling on the handle
    // affects the branch only); specialize_fail() terminates the chain with no
    // fallback. Shared by Func (pure stage) and Stage (update stage) -- this is
    // ONE operation on whichever stage `stage_index` names, so it lives here in
    // FuncStageImpl, NOT duplicated in Func and Stage (see the header note on
    // FuncStageImpl). Defined out of line below (they return Stage, incomplete
    // here).
    class Stage specialize(const Expr &condition);
    void specialize_fail(const std::string &message);
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

    // (Func, Var) names a "site", a.k.a. a loop at which we can inject the realization of this function.
    // A reduction variable / 1-D RDom names a loop too, so it can be a
    // compute_at site (loopdoc.md section 10). Both can convert to VarOrRVar, which exposes .name()
    Func &compute_at(const Func &f, const VarOrRVar &var)
    {
        contents->level = FuncContents::Level::At;
        contents->at_func = f.contents;
        contents->at_var = var.name();
        return *this;
    }

    // compute_inline(): reset the compute level to the default (inlined) -- the
    // inverse of compute_root/compute_at.
    Func &compute_inline()
    {
        contents->level = FuncContents::Level::Inline;
        contents->at_func = {};
        contents->at_var = {};
        return *this;
    }

    // store_at / store_root: record the store level (loopdoc.md section 8). See
    // the note on FuncContents; the loop-nest `store` node and its legality are
    // emitted/checked by the printer.
    Func &store_at(const Func &f, const VarOrRVar &var)
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
    // has NO effect on the printed nest; only legality depends on it (loopdoc.md
    // section 8).
    Func &hoist_storage(const Func &f, const VarOrRVar &var)
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

    // in() / clone_in() (loopdoc.md section 13): create a wrapper / clone Func
    // that the named consumer(s) read instead of this Func, and return it. They
    // record the wrapper on THIS Func keyed by consumer; the consumer reads are
    // resolved at nest-construction time via the producer-accessor seam.
    Func in(const Func &consumer);
    Func in(const std::vector<Func> &consumers);
    Func in();
    Func clone_in(const Func &consumer);
    Func clone_in(const std::vector<Func> &consumers);

    // compute_with, specialize, and specialize_fail are inherited from
    // FuncStageImpl (they work for Func and Stage alike); do NOT redeclare them
    // here (see the header note on FuncStageImpl).

    void print_loop_nest();
};

// ---------------------------------------------------------------------------
// Stage: a handle to one update stage's schedule, returned by Func::update(i).
// The loop transforms (split/fuse/reorder/tile, compute_with, specialize) are
// inherited from FuncStageImpl and rewrite THIS stage's dimension list (which
// may contain RVars); see loopdoc.md sections 6 and 10.
// ---------------------------------------------------------------------------
class Stage: public FuncStageImpl<Stage>
{
  public:
    Stage(std::shared_ptr<FuncContents> f, int i) : FuncStageImpl(std::move(f), i + 1)
    {
    }

    // Branch-handle constructor (loopdoc.md section 15): addresses a
    // specialization branch's forked schedule `br` rather than a base stage.
    // `stage_idx` is the OWNING base stage index (already 0-based over
    // contents->stages), kept only so owner()/legality can find the Func; all
    // scheduling goes through `branch`. Distinguished from the 2-arg update
    // constructor by arity (it does not apply the +1 update offset).
    Stage(std::shared_ptr<FuncContents> f, int stage_idx, std::shared_ptr<StageData> br)
      : FuncStageImpl(std::move(f), stage_idx)
    {
        branch = std::move(br);
    }

    // rfactor (loopdoc.md section 12): factor THIS update stage's associative
    // reduction into a new intermediate Func plus a rewritten merge stage. It
    // CREATES a new Func (returned) and MUTATES this stage, building the
    // intermediate's stages and rewriting the merge per loopdoc.md section 12.
    Func rfactor(const RVar &r, const Var &v)
    {
        return rfactor(std::vector<std::pair<RVar, Var>>{{r, v}});
    }
    Func rfactor(const std::vector<std::pair<RVar, Var>> &preserved);

    // compute_with, specialize, and specialize_fail are inherited from
    // FuncStageImpl; do NOT redeclare them here (see the header note on
    // FuncStageImpl). Nested specialization of a branch just calls the inherited
    // specialize() on the Stage handle a prior specialize() returned.
};

inline Stage Func::update(int i) const
{
    return Stage(contents, i);
}

// specialize / specialize_fail (loopdoc.md section 15), defined ONCE for both
// Func and Stage on the FuncStageImpl base (out of line, because they return
// Stage which is only complete now).
//
// specialize(cond) appends a specialization to THIS handle's stage (its base
// stage, or -- when this is itself a branch handle -- that branch's own list,
// giving nesting). The new branch's schedule is a COPY OF THE SCHEDULE SO FAR
// (all directives issued before this call: the current dims, collapse set,
// producers, fuse edge), but with an EMPTY specialization list of its own
// (loopdoc.md section 1: each fork is a full definition starting fresh). A
// handle to that fork is returned, so later directives on it affect the branch
// only; directives on the original handle after this call modify the parent
// (fallback) instead. The condition is stored but never printed.
template <typename Derived>
inline Stage FuncStageImpl<Derived>::specialize(const Expr &condition)
{
    StageData &s = stage();
    auto fork = std::make_shared<StageData>(s);  // copy the schedule so far
    fork->specializations.clear();               // the fork starts with none (section 1)
    fork->specialize_failed = false;
    s.specializations.push_back(Specialization{condition, fork});
    return Stage(contents, stage_index, fork);
}

// specialize_fail(msg) terminates THIS handle's specialization chain: the final
// `else` becomes a run-time assertion, so the fallback nest is dropped and only
// the specialization branches print (loopdoc.md section 15). Nothing may be
// specialized after it (not enforced here; no example exercises it).
template <typename Derived>
inline void FuncStageImpl<Derived>::specialize_fail(const std::string &)
{
    stage().specialize_failed = true;
}

// ---------------------------------------------------------------------------
// in() / clone_in() (loopdoc.md section 13). Both create a new, separate Func
// that a chosen set of consumers read instead of the wrapped Func `f`:
//   * an `in` wrapper is a pure pointwise reader of `f` (its single producer is
//     `f`); left at the default inline level it is a pure inline Func and is
//     substituted away (so unscheduled it has no effect), and it always keeps
//     `f` in the pipeline (the wrapper reads `f`).
//   * a clone is an independent copy of `f`'s OWN definition (all stages) whose
//     calls still point at the SAME callees (callees are shared, not copied):
//     so the clone reads `f`'s inputs, not `f`. If every consumer of `f` is
//     redirected to the clone, `f` becomes unreachable and drops out.
//
// We do NOT mutate the named consumers here. We record the new Func on `f`,
// keyed by the redirected *direct-caller* consumers; the actual reads are
// rewritten by the producer-accessor seam at nest-construction time
// (loopdoc.md section 13 "Implementation note"). Transitive-caller
// normalization ("f.in(h)" where h reaches f only through g redirects g) IS
// resolved here, since the producer graph already exists by the time in()/
// clone_in() is called for a named consumer.
namespace internal
{
// Resolve the pin target(s) for wrapping `f` for a named `consumer`, per the
// §13 search: descend `consumer`'s calls in the current graph; the FIRST Func on
// each branch that directly calls `f` is pinned, and that branch is NOT descended
// further (loopdoc.md section 13 pin_targets). So a Func that reads `f` directly
// is pinned even if it ALSO reaches `f` through a callee below it -- that lower
// caller keeps reading `f` (the "named consumer is usually not the Func modified"
// / partial-routing surprise: clone_in(out) pins c2, and c1 beneath c2 stays on
// the original). If `consumer` itself directly calls `f`, it is its own pin.
inline void collect_direct_callers(FuncContents *node, FuncContents *f,
                                    std::set<FuncContents *> &seen,
                                    std::set<FuncContents *> &out)
{
    if (!seen.insert(node).second)
    {
        return;
    }
    if (node == f)
    {
        return;
    }
    // Read the CURRENT graph (loopdoc.md section 13 pin_targets) from the
    // current call graph (all_producers reads it live from the stages, so a read
    // of f that an eager rewrite such as rfactor (§12) has moved into an
    // intermediate is correctly no longer seen as a direct call to f).
    std::vector<FuncContents *> callees;
    bool calls_f = false;
    for (auto &p : all_producers(node))
    {
        if (p.get() == f)
        {
            calls_f = true;
        }
        else
        {
            callees.push_back(p.get());
        }
    }
    // Does this Func directly call f? If so, pin it and stop descending this
    // branch (do not recurse into its other callees looking for deeper pins).
    if (calls_f)
    {
        out.insert(node);
        return;
    }
    for (FuncContents *c : callees)
    {
        collect_direct_callers(c, f, seen, out);
    }
}
} // namespace internal

inline Func Func::in(const std::vector<Func> &consumers)
{
    // One shared wrapper for all the named consumers (loopdoc.md section 13). A
    // CUSTOM wrapper's name embeds its (first) named consumer (Halide names it
    // `f_in_g1`) so it is a DISTINCT node from a global `f.in()` wrapper (named
    // `f_in`) -- the two must not collapse to one identity when both coexist
    // (in_custom_and_global.cpp).
    std::string wname = contents->name + "_in";
    if (!consumers.empty())
    {
        wname += "_" + consumers.front().contents->name;
    }
    Func w(wname);
    StageData s0;
    s0.dims = contents->stages[0].dims; // identity wrapper: same dimensions as f
    s0.producers = {contents};          // the wrapper reads f
    w.contents->stages.push_back(std::move(s0));
    w.contents->defined = true;

    for (const Func &c : consumers)
    {
        std::set<FuncContents *> seen, callers;
        internal::collect_direct_callers(c.contents.get(), contents.get(), seen, callers);
        // No Func on any branch below the named consumer calls f: fall back to
        // pinning the consumer itself (loopdoc.md section 13 pin_targets). Such a
        // pin typically fails the lowering re-check, since the consumer does not
        // call f -- run() surfaces that.
        if (callers.empty())
        {
            callers.insert(c.contents.get());
        }
        for (FuncContents *caller : callers)
        {
            contents->wrappers[caller] = w.contents;
        }
    }
    return w;
}

inline Func Func::in(const Func &consumer)
{
    return in(std::vector<Func>{consumer});
}

inline Func Func::in()
{
    // A single GLOBAL wrapper used by every consumer with no custom wrapper of
    // its own (loopdoc.md section 13). Redirection is resolved at build time.
    Func w(contents->name + "_in");
    StageData s0;
    s0.dims = contents->stages[0].dims;
    s0.producers = {contents};
    w.contents->stages.push_back(std::move(s0));
    w.contents->defined = true;
    contents->global_wrapper = w.contents;
    return w;
}

// Recursively deep-copy a stage's schedule, INCLUDING its specialization tree
// (loopdoc.md sections 13, 15): a clone is an independent copy, so its branches
// must be fresh StageData objects, not shared_ptrs aliasing the source's. The
// default StageData copy would share the branch shared_ptrs; this rebuilds them.
inline StageData deep_copy_stage(const StageData &s)
{
    StageData out = s;             // copies dims/collapse/producers/rvars/fuse/bools
    out.specializations.clear();   // rebuild the branch subtree independently
    for (const Specialization &sp : s.specializations)
    {
        out.specializations.push_back(
            Specialization{sp.condition,
                           std::make_shared<StageData>(deep_copy_stage(*sp.schedule))});
    }
    return out;
}

inline Func Func::clone_in(const std::vector<Func> &consumers)
{
    // An independent clone: a COPY of f's entire definition (all stages, their
    // specializations, and the producer set), but the callees are SHARED (the
    // copied stages reference the same producer shared_ptrs). The clone reads
    // f's inputs, not f (loopdoc.md section 13). The stage copy is DEEP through
    // the specialization tree (loopdoc.md section 15): the clone carries an
    // independent copy of f's branches, unlike an in() wrapper, which is a fresh
    // pointwise Func with no specializations.
    Func w(contents->name + "_clone_in");
    for (const StageData &st : contents->stages)   // deep-copy each stage + branches
    {
        w.contents->stages.push_back(deep_copy_stage(st));
    }
    // The deep-copied stages already carry f's callees (shared shared_ptrs), so
    // the clone's producer set falls out of all_producers(clone); nothing to set.
    w.contents->defined = true;
    // A clone must NOT inherit f's wrapper registry (those wrappers wrap f, not
    // the clone).
    w.contents->wrappers.clear();
    w.contents->global_wrapper.reset();

    for (const Func &c : consumers)
    {
        std::set<FuncContents *> seen, callers;
        internal::collect_direct_callers(c.contents.get(), contents.get(), seen, callers);
        // No path from the named consumer down to f: fall back to pinning the
        // consumer itself (loopdoc.md section 13 pin_targets), which then fails
        // the lowering re-check because the consumer does not call f.
        if (callers.empty())
        {
            callers.insert(c.contents.get());
        }
        for (FuncContents *caller : callers)
        {
            contents->wrappers[caller] = w.contents;
        }
    }
    return w;
}

inline Func Func::clone_in(const Func &consumer)
{
    return clone_in(std::vector<Func>{consumer});
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
        throw CompileError(
            "micro_halide: rfactor may only be called on an update stage, "
            "not the pure stage (loopdoc.md section 12)");
    }

    FuncContents *orig = contents.get();
    // The definition rfactor edits is whichever the handle ADDRESSES: a
    // specialization branch's forked copy when this is a branch handle
    // (g.update(n).specialize(cond).rfactor(...) factors only that branch),
    // otherwise the base stage (loopdoc.md section 12 "the edit lands on
    // whichever definition the handle addresses"; section 15; section 1). Using
    // stage() (not contents->stages[stage_index]) makes rfactor compose with
    // specialize orthogonally: the branch's LHS/RHS is rewritten while the base
    // fallback and sibling branches keep their original definitions.
    StageData &update = stage();

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
        intm_pure.dims.push_back(DimData(p.second.name(), false));
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
            intm_update.dims.push_back(DimData(it->second, false));
        }
        else
        {
            // A non-preserved RVar stays a reduction loop here.
            DimData dim_copy(d);
            dim_copy._is_rvar = dimlist::is_rvar_name(update.dims, d.name());
            intm_update.dims.push_back(dim_copy);
        }
    }
    // It reads whatever the original update read.
    intm_update.producers = update.producers;

    intm.contents->stages.push_back(std::move(intm_pure));
    intm.contents->stages.push_back(std::move(intm_update));

    // ---- Rewrite the original chosen update stage into the merge ----------
    std::vector<DimData> merged;
    std::set<std::string> merged_rvars;
    for (const DimData &d : update.dims)
    {
        bool is_rvar = dimlist::is_rvar_name(update.dims, d.name());
        if (is_rvar && !preserved_rvars.count(d.name()))
        {
            continue; // non-preserved RVar: lifted into the intermediate
        }
        DimData dim_copy(d);
        dim_copy._is_rvar = is_rvar;
        merged.push_back(dim_copy); // free Var, or preserved RVar (stays an RVar here)
    }
    update.dims = std::move(merged);
    // The merge now reads only the intermediate. Because the whole-Func producer
    // set is derived from the stages (all_producers), rewriting this stage's
    // reads is enough: `intm` becomes a producer of the original Func and the old
    // callee (now read only by `intm`) drops out automatically -- no stale
    // func-level edge to prune. When the handle addresses a specialization branch
    // (§15), the edit lands on that branch's forked stage, and all_producers
    // still picks `intm` up by walking the branch tree.
    update.producers = {intm.contents};

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
        s0.dims.push_back(DimData("_" + std::to_string(i), false));
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

    // A loop level inside a particular STAGE of a site_func: (site_func, stage, var-name).
    // With update definitions (loopdoc.md section 10) a site_func emits one loop nest
    // per stage inside a single `produce`, so an injection/store site is pinned
    // to a specific stage (an RVar loop, for instance, exists only in its own
    // stage).
    using SiteKey = std::tuple<FuncContents *, int, std::string>;

    // funcs computed_at a given stage-loop level, in realization order.
    std::map<SiteKey, std::vector<FuncContents *>> children_at;

    // funcs whose `store` node opens at a given stage-loop level (store_at with
    // a store level outer to the compute level), in realization order. A
    // `store f:` node here wraps everything emitted deeper at that level -- the
    // site_func loops between the store and compute levels, and f's own
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

    // ---- Fused groups (loopdoc.md section 14) ----------------------------
    // A fused group is the connected component of Funcs joined by compute_with
    // fuse edges (edges join stages, so one Func can sit on several edges). The
    // whole group is realized as one unit at one compute level: it is emitted as
    // a single interleaved sequence of stage nests, wrapped by a produce/consume
    // for every member (loopdoc.md sections 14 + 15).
    struct FuseGroup
    {
        std::vector<FuncContents *> members;       // all Funcs in the group
        FuncContents *spine_owner = nullptr;       // group parent / produce anchor
        std::vector<FuncContents *> realize_order; // within-group realization order
        std::vector<FuncContents *> produce_order; // outermost produce first
    };
    // Map each Func that is in a (multi-member) fused group to its group.
    std::map<FuncContents *, std::shared_ptr<FuseGroup>> group_of_;

    // The group a Func belongs to, or nullptr if it is not fused at all.
    std::shared_ptr<FuseGroup> group_of(FuncContents *f)
    {
        auto it = group_of_.find(f);
        return it == group_of_.end() ? nullptr : it->second;
    }

    // Build the fused groups from the recorded fuse edges over `order` (every
    // reachable Func). Union members connected by edges; for each multi-member
    // group compute the spine owner, the within-group realization order, and the
    // produce nesting order (loopdoc.md section 14 "Member ordering").
    void build_groups(const std::vector<FuncContents *> &order)
    {
        // Union-find by Func pointer.
        std::map<FuncContents *, FuncContents *> parent;
        std::function<FuncContents *(FuncContents *)> find =
            [&](FuncContents *x) -> FuncContents * {
            while (parent[x] != x)
            {
                parent[x] = parent[parent[x]];
                x = parent[x];
            }
            return x;
        };
        auto unite = [&](FuncContents *a, FuncContents *b) {
            parent[find(a)] = find(b);
        };
        for (FuncContents *f : order)
        {
            if (parent.find(f) == parent.end())
            {
                parent[f] = f;
            }
        }
        bool any_edge = false;
        for (FuncContents *f : order)
        {
            for (const StageData &s : f->stages)
            {
                if (s.has_fuse && s.fuse_parent)
                {
                    FuncContents *p = s.fuse_parent.get();
                    if (parent.find(p) == parent.end())
                    {
                        parent[p] = p;
                    }
                    unite(f, p);
                    any_edge = true;
                }
            }
        }
        if (!any_edge)
        {
            return;
        }
        // Collect members per representative.
        std::map<FuncContents *, std::vector<FuncContents *>> comp;
        for (FuncContents *f : order)
        {
            comp[find(f)].push_back(f);
        }
        for (auto &kv : comp)
        {
            std::vector<FuncContents *> &members = kv.second;
            if (members.size() < 2)
            {
                continue; // not actually fused
            }
            auto grp = std::make_shared<FuseGroup>();
            grp->members = members;
            // §6 tie-break order of the members.
            std::sort(grp->members.begin(), grp->members.end(),
                      [this](FuncContents *a, FuncContents *b) {
                          return sort_key(a) < sort_key(b);
                      });
            // Member order (loopdoc.md section 14 step 1): a topological sort
            // of the members with each CHILD before its PARENT, breaking what
            // the fuse edges leave unordered by the §6 tie-break. At Func
            // granularity, C is a child of M iff some stage of C fuses into some
            // stage of M; child_members[M] is that set. A member is *ready* to
            // be placed once all its children are placed; among ready members we
            // pick the §6-smallest (grp->members is already §6-sorted, so the
            // first ready one in that vector is the smallest). This yields
            // children before parents (a chain comes out deepest-child-first),
            // and the LAST member placed is the spine owner.
            std::set<FuncContents *> member_set(grp->members.begin(),
                                                grp->members.end());
            std::map<FuncContents *, std::set<FuncContents *>> child_members;
            for (FuncContents *c : grp->members)
            {
                for (const StageData &s : c->stages)
                {
                    if (s.has_fuse && s.fuse_parent)
                    {
                        FuncContents *p = s.fuse_parent.get();
                        if (p != c && member_set.count(p))
                        {
                            child_members[p].insert(c);
                        }
                    }
                }
            }
            std::set<FuncContents *> placed;
            while (grp->realize_order.size() < grp->members.size())
            {
                FuncContents *best = nullptr;
                for (FuncContents *m : grp->members) // §6-sorted
                {
                    if (placed.count(m))
                    {
                        continue;
                    }
                    bool ready = true;
                    auto cit = child_members.find(m);
                    if (cit != child_members.end())
                    {
                        for (FuncContents *c : cit->second)
                        {
                            if (!placed.count(c))
                            {
                                ready = false;
                                break;
                            }
                        }
                    }
                    if (ready)
                    {
                        best = m; // first ready in §6 order = §6-smallest ready
                        break;
                    }
                }
                if (!best)
                {
                    // No member is ready though some remain: the child-before-parent
                    // constraints form a cycle -- two members fuse into each other
                    // (loopdoc.md section 14 "Legality": no cyclic fuse edges).
                    // Halide errors "Found cyclic dependencies between compute_with".
                    for (FuncContents *m : grp->members)
                    {
                        if (!placed.count(m))
                        {
                            fail(m, "compute_with: found cyclic dependencies between "
                                    "the compute_with of two Funcs (each fuses into "
                                    "the other)");
                        }
                    }
                }
                grp->realize_order.push_back(best);
                placed.insert(best);
            }
            grp->spine_owner = grp->realize_order.back();
            // produce nesting = reverse of realization order (last-realized
            // outermost).
            grp->produce_order.assign(grp->realize_order.rbegin(),
                                      grp->realize_order.rend());
            for (FuncContents *m : grp->members)
            {
                group_of_[m] = grp;
            }
        }
    }

    // The single compute level shared by all members of a group (loopdoc.md
    // section 14: all members share one compute level). We read it off the spine
    // owner (validated equal across members in validate_groups).
    FuncContents *group_at_func(FuseGroup *grp)
    {
        return grp->spine_owner->at_func.get();
    }

    // ---- Group legality (loopdoc.md section 14 "Legality") ---------------
    // Validate each fused group's preconditions, separate from §7's compute_at
    // rule. Called from validate().
    void validate_groups(const std::vector<FuncContents *> &order)
    {
        for (FuncContents *f : order)
        {
            for (int cs = 0; cs < num_stages(f); cs++)
            {
                const StageData &child = f->stages[cs];
                if (!child.has_fuse || !child.fuse_parent)
                {
                    continue;
                }
                FuncContents *p = child.fuse_parent.get();
                int ps = child.fuse_parent_stage;
                const std::string &v = child.fuse_var;

                // The Func that CALLS compute_with must have no specializations
                // (loopdoc.md section 15 Legality): a fused group is emitted as
                // one shared, unconditional loop nest, with no room for a
                // member's per-branch variants. The restriction is on the caller
                // f, not the target p (which may be specialized).
                for (int ss = 0; ss < num_stages(f); ss++)
                {
                    if (!f->stages[ss].specializations.empty() ||
                        f->stages[ss].specialize_failed)
                    {
                        fail(f, "compute_with: the Func that calls compute_with "
                                "must not have any specializations");
                    }
                }

                // No producer/consumer dependency between the fused Funcs.
                if (reaches(f, p) || reaches(p, f))
                {
                    fail(f, "compute_with: there is a producer/consumer dependency "
                            "between the fused Funcs");
                }

                // `v` must exist by name in BOTH fused stages.
                int ci = stage_dim_index(f, cs, v);
                int pi = stage_dim_index(p, ps, v);
                if (pi < 0)
                {
                    fail(f, "compute_with: the fuse level does not name a loop of the "
                            "parent stage");
                }
                if (ci < 0)
                {
                    fail(f, "compute_with: the fuse level does not name a loop of the "
                            "child stage");
                }
                // The number of loops from the outermost down to `v` must match
                // (so `v` sits at the same depth). dims[0] is innermost; the count
                // of loops at/above `v` is (size - index).
                int child_above = (int)stage_dims(f, cs).size() - ci;
                int parent_above = (int)stage_dims(p, ps).size() - pi;
                if (child_above != parent_above)
                {
                    fail(f, "compute_with: the number of fused dimensions (loops down "
                            "to the fuse level) of the two stages do not match");
                }

                // All group members must share ONE compute level.
                if (!same_compute_level(f, p))
                {
                    fail(f, "compute_with: the compute levels of the fused Funcs do "
                            "not match");
                }
            }
        }
        // Cross-member compute-level agreement for the whole group (covers chains
        // and multi-parent groups, where the spine owner may not be on every
        // edge).
        for (auto &kv : group_of_)
        {
            FuseGroup *grp = kv.second.get();
            for (FuncContents *m : grp->members)
            {
                if (!same_compute_level(m, grp->spine_owner))
                {
                    fail(m, "compute_with: all members of a fused group must share one "
                            "compute level");
                }
            }
        }

        // The stage order must exist (loopdoc.md section 14 "Legality": "The stage
        // order ... must exist"). A Func's own stages are forced into order, so as
        // a child Func's stages advance, the PARENT-stage index they fuse into
        // must be NON-DECREASING, and may repeat only across CONSECUTIVE fused
        // child stages (no stage in between). Checked per child Func, per parent
        // Func. Two failure shapes:
        //   * a decrease            -- f.s0->g.s1 but f.s1->g.s0 (crossing_edges2)
        //   * a repeat across a gap -- f.s0 and f.s2 -> g.s0, f.s1 between
        //                              (crossing_edges1)
        // Either way no consistent order exists.
        for (FuncContents *f : order)
        {
            // Gather f's fuse edges grouped by parent Func, in child-stage order.
            std::map<FuncContents *, std::vector<std::pair<int, int>>> by_parent;
            for (int cs = 0; cs < num_stages(f); cs++)
            {
                const StageData &child = f->stages[cs];
                if (child.has_fuse && child.fuse_parent)
                {
                    by_parent[child.fuse_parent.get()].emplace_back(
                        cs, child.fuse_parent_stage);
                }
            }
            for (auto &pe : by_parent)
            {
                const std::vector<std::pair<int, int>> &edges = pe.second;
                for (size_t i = 1; i < edges.size(); i++)
                {
                    int prev_cs = edges[i - 1].first, prev_ps = edges[i - 1].second;
                    int cur_cs = edges[i].first, cur_ps = edges[i].second;
                    if (cur_ps < prev_ps)
                    {
                        fail(f, "compute_with: impossible to establish correct stage "
                                "order (a later child stage fuses into an earlier "
                                "parent stage)");
                    }
                    if (cur_ps == prev_ps && cur_cs != prev_cs + 1)
                    {
                        fail(f, "compute_with: impossible to establish correct stage "
                                "order (two child stages fuse into the same parent "
                                "stage with a stage in between)");
                    }
                }
            }
        }
    }

    // Do the two Funcs have the SAME compute level (loopdoc.md section 14)?
    static bool same_compute_level(FuncContents *a, FuncContents *b)
    {
        if (a->level != b->level)
        {
            return false;
        }
        if (a->level == FuncContents::Level::At)
        {
            return a->at_func.get() == b->at_func.get() && a->at_var == b->at_var;
        }
        return true; // both Root, or both Inline
    }

    // Does `a` (transitively) read `b` (a producer/consumer dependency)?
    bool reaches(FuncContents *a, FuncContents *b)
    {
        std::set<FuncContents *> seen;
        return reaches_rec(a, b, seen);
    }
    bool reaches_rec(FuncContents *a, FuncContents *b, std::set<FuncContents *> &seen)
    {
        if (a == b)
        {
            return true;
        }
        if (!seen.insert(a).second)
        {
            return false;
        }
        for (auto &p : func_producers(a))
        {
            if (reaches_rec(p.get(), b, seen))
            {
                return true;
            }
        }
        return false;
    }

    // ---- Group stage ordering + emission (loopdoc.md sections 14 + 15) ----

    // A stage identified by (Func, stage index).
    using StageId = std::pair<FuncContents *, int>;

    // Order all stages of a group's members into the single stage order
    // (loopdoc.md section 14 step 2: "Emit the stages, in a repeated sweep").
    // Walk the members in step-1 realization order and emit each member's stages
    // in order (s0, s1, ...) for as long as the next one is *ready*; when it is
    // not, move on to the next member, and keep sweeping until every stage is
    // placed. A stage is ready once all earlier stages of its own Func are
    // placed (guaranteed by advancing each member strictly in order) and -- if
    // it is fused -- its parent stage is placed. A member whose next stage is
    // blocked is skipped this sweep and revisited later, so its stage can land
    // after stages of members ordered AFTER it ("The two observable orders").
    std::vector<StageId> group_stage_order(FuseGroup *grp)
    {
        int total = 0;
        for (FuncContents *m : grp->members)
        {
            total += num_stages(m);
        }
        std::set<StageId> done;
        std::vector<StageId> result;
        std::map<FuncContents *, int> next_stage; // next unplaced stage per member
        for (FuncContents *m : grp->members)
        {
            next_stage[m] = 0;
        }
        bool progress = true;
        while ((int)result.size() < total && progress)
        {
            progress = false;
            for (FuncContents *m : grp->realize_order)
            {
                int &ns = next_stage[m];
                while (ns < num_stages(m))
                {
                    const StageData &sd = m->stages[ns];
                    // Blocked iff fused and the parent stage is not yet placed.
                    if (sd.has_fuse && sd.fuse_parent &&
                        !done.count({sd.fuse_parent.get(), sd.fuse_parent_stage}))
                    {
                        break; // stall this member for this sweep
                    }
                    result.emplace_back(m, ns);
                    done.insert({m, ns});
                    ns++;
                    progress = true;
                }
            }
        }
        return result;
    }

    // Emit a fused group's interleaved body at `indent` (loopdoc.md section 14
    // step 2). The body is the sequence of TOP-LEVEL nests -- one per *unfused*
    // stage -- in stage order; each fused child stage is spliced into its
    // parent's nest (shared loops down to the fuse level, child's below-level
    // loops as siblings). The produce/consume wrapper is added by the caller.
    void emit_group_body(FuseGroup *grp, int indent)
    {
        std::vector<StageId> order = group_stage_order(grp);
        // Children of each (parent) stage, in stage order.
        std::map<StageId, std::vector<StageId>> children;
        for (const StageId &s : order)
        {
            const StageData &sd = s.first->stages[s.second];
            if (sd.has_fuse && sd.fuse_parent)
            {
                children[{sd.fuse_parent.get(), sd.fuse_parent_stage}].push_back(s);
            }
        }
        // Emit one nest per unfused stage, in stage order.
        for (const StageId &s : order)
        {
            const StageData &sd = s.first->stages[s.second];
            if (sd.has_fuse)
            {
                continue; // spliced into its parent's nest
            }
            // An unfused stage starts its own nest with all loops real: no extra
            // collapse floor (floor = dim count, so no dim index reaches it).
            int ndims = (int)stage_dims(s.first, s.second).size();
            emit_fused_stage(s, ndims - 1, indent, children, /*collapse_floor=*/ndims);
        }
    }

    // Emit one fused nest rooted at stage `node`, walking `node`'s dimension
    // list from `dim` inward. `collapse_floor` is the dimension index at/above
    // which this stage's loops are collapsed extent-1 scheduling points
    // (loopdoc.md section 14 "Loop ownership"): they print no `for` and add no
    // indent, but each is still a valid injection / splice site. The spine owner
    // and every unfused stage are called with collapse_floor == the dim count
    // (nothing extra collapsed); a spliced (fused) member is called with
    // collapse_floor == the index of its fuse level, so its loops from the
    // outermost down to the fuse level all collapse to its splice position, while
    // its loops below the fuse level re-materialize as real loops nested there.
    void emit_fused_stage(StageId node, int dim, int indent,
                          std::map<StageId, std::vector<StageId>> &children,
                          int collapse_floor)
    {
        FuncContents *f = node.first;
        int stage = node.second;

        if (dim < 0)
        {
            // Innermost point: just this node's leaf. (Children are spliced at
            // their fuse var's level, handled below when we reach that level.)
            out << pad(indent) << f->name << "(...) = ...\n";
            return;
        }

        const std::string &var = stage_dims(f, stage)[dim].name();
        // A loop is elided if declared collapsed (bounds, §7) OR it is at/above
        // this stage's collapse floor (a non-spine member's shared loop, §14).
        bool elided = stage_collapsed(f, stage).count(var) != 0 || dim >= collapse_floor;
        int body_indent = indent;
        if (!elided)
        {
            out << pad(indent) << "for " << var << ":\n";
            body_indent = indent + 2;
        }

        // Direct children of THIS stage fused at THIS var (non-recursive): each
        // is spliced here. Deeper chain members are reached when we recursively
        // walk each spliced member (a member fused into a collapsed loop of a
        // non-spine parent re-materializes there -- compute_with_chain_outer).
        std::vector<StageId> spliced;
        auto cit = children.find(node);
        if (cit != children.end())
        {
            for (const StageId &c : cit->second)
            {
                if (c.first->stages[c.second].fuse_var == var)
                {
                    spliced.push_back(c);
                }
            }
        }

        // Producers (compute_at) and store nodes filed at this loop level belong
        // to this stage (loopdoc.md section 14 "Loop ownership"); a collapsed
        // loop is still a valid site. Emit them wrapping the body.
        SiteKey key{f, stage, var};
        auto kit = children_at.find(key);
        std::vector<FuncContents *> empty;
        const std::vector<FuncContents *> &kids =
            (kit == children_at.end()) ? empty : kit->second;

        auto body = [this, dim, node, collapse_floor, &spliced, &children](int ind) {
            // This node's own continuation inward, then each spliced member,
            // walked from its top with its fuse level as the collapse floor.
            emit_fused_stage(node, dim - 1, ind, children, collapse_floor);
            for (const StageId &c : spliced)
            {
                int ctop = (int)stage_dims(c.first, c.second).size() - 1;
                int cfloor = stage_dim_index(c.first, c.second,
                                             c.first->stages[c.second].fuse_var);
                emit_fused_stage(c, ctop, ind, children, cfloor);
            }
        };

        // compute_at producers filed here wrap the body (their consume around
        // it); then store nodes; mirrors emit_dim.
        auto inject = [this, &kids, &body](int ind) {
            emit_realizations(kids, 0, ind, body, /*has_cont=*/true);
        };
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

    // Collect a Func's producers in DEFINITION (first-appearance) VISITATION
    // order (loopdoc.md section 6): stages are walked in order (pure, then each
    // update), and *within a stage the base definition's calls are visited before
    // any specialization branch's calls* (recursively, branches in declaration
    // order). This is the tie-break between sibling producers that share a name
    // prefix -- e.g. two rfactor intermediates both named "<orig>_intm", one read
    // by the base definition and one only by a specialization branch: the base
    // one is visited (hence realized) first. Wrapper/clone redirection is applied
    // so the walk sees the resolved graph.
    void collect_branch_visit_producers(FuncContents *f, const StageData &st,
                                         std::vector<FuncContents *> &out)
    {
        for (const auto &spec : st.specializations)
        {
            for (auto &p : spec.schedule->producers)
            {
                out.push_back(redirect(p, f).get());
            }
            collect_branch_visit_producers(f, *spec.schedule, out);
        }
    }

    std::vector<FuncContents *> visit_producers(FuncContents *f)
    {
        std::vector<FuncContents *> out;
        for (int s = 0; s < (int)f->stages.size(); s++)
        {
            for (auto &p : stage_producers(f, s))
            {
                out.push_back(p.get());
            }
            collect_branch_visit_producers(f, f->stages[s], out);
        }
        return out;
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
        for (FuncContents *p : visit_producers(f))
        {
            compute_visit_order(p, seen, counter);
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

    // Groups whose contracted vertex has already been appended (see below).
    std::set<FuseGroup *> realized_groups_;

    // Post-order DFS over producers (producers before consumers), visiting a
    // Func's producers in realization-order tie-break order.
    //
    // A fused group (§14) is a single CONTRACTED VERTEX in this graph (loopdoc.md
    // §6 "Fused groups: one contracted vertex"): reaching ANY member processes the
    // WHOLE group as one node, whose out-edges are the UNION of the members'
    // producers -- so the group is appended once, after everything any member
    // reads and before every consumer of any member. The edge that led here is
    // still sorted by the *member's* key (the caller sorts its producers by
    // sort_key of the actual producer, which for a member is that member's key --
    // the "edge label"), so which member a consumer reads decides where the group
    // sorts among that consumer's other producers.
    void realization_order(FuncContents *f, std::set<FuncContents *> &visited,
                           std::vector<FuncContents *> &order)
    {
        std::shared_ptr<FuseGroup> grp = group_of(f);
        if (grp)
        {
            if (!realized_groups_.insert(grp.get()).second)
            {
                return; // the group's contracted vertex is already placed
            }
            for (FuncContents *m : grp->members)
            {
                visited.insert(m);
            }
            // Out-edges of the contracted vertex: the union of every member's
            // producers, minus intra-group edges (a producer that is itself a
            // member does not leave the vertex). Sorted by the producer's key.
            std::vector<FuncContents *> prods;
            std::set<FuncContents *> pseen;
            for (FuncContents *m : grp->members)
            {
                for (auto &p : func_producers(m))
                {
                    if (group_of(p.get()) != grp && pseen.insert(p.get()).second)
                    {
                        prods.push_back(p.get());
                    }
                }
            }
            std::sort(prods.begin(), prods.end(),
                      [this](FuncContents *a, FuncContents *b) { return sort_key(a) < sort_key(b); });
            for (FuncContents *p : prods)
            {
                realization_order(p, visited, order);
            }
            // Append the members as one contiguous block (their within-group order
            // is decided separately by build_groups; the block's POSITION is the
            // contracted vertex's slot).
            for (FuncContents *m : grp->realize_order)
            {
                order.push_back(m);
            }
            return;
        }

        if (!visited.insert(f).second)
        {
            return;
        }
        std::vector<FuncContents *> prods;
        for (auto &p : func_producers(f))
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

    // THE WRAPPER-RESOLUTION SEAM (loopdoc.md section 13, in()/clone_in()).
    // Every producer read during nest construction goes through one of these two
    // accessors, keyed by the CONSUMER `f`. Today they return the stored producer
    // lists verbatim. When in()/clone_in() is implemented, the redirection of a
    // consumer's reads to a wrapper/clone is inserted HERE (and only here): for
    // consumer `f`, a producer that has a wrapper registered for `f` is swapped
    // for that wrapper. Nothing else in the builder reads `->producers` directly,
    // so the rest of the nest logic needs no changes. (Mirrors Halide's
    // wrap_func_calls, which records the wrapper on the WRAPPED Func and resolves
    // consumer calls as a derived pass -- it does not mutate consumers at in()
    // time; see src_doc section 13.)
    //
    // RESOLUTION TIMING: do the consumer->wrapper substitution ONCE, up front --
    // a single pass at the start of nest construction that fills a backing store
    // these accessors then return from -- rather than recomputing it on every
    // call. These accessors are hot (the realization-order DFS, body_uses /
    // g_uses_f recursion, stage_reads, store-node placement all go through them),
    // and one-time resolution keeps the returned shared_ptrs stable so the
    // p.get()==f identity comparisons throughout the builder stay consistent, and
    // lets both accessors keep returning by const-ref. This mirrors
    // wrap_func_calls building the substituted env once before lowering. (A
    // global f.in() in particular CANNOT be resolved eagerly at in() time: it
    // must redirect every consumer reachable from the output, including ones
    // defined after the in() call -- so deferred, build-time resolution is
    // required, not merely preferred.)
    //
    // Backing store filled once by resolve_wrappers() at the start of print().
    // When no in()/clone_in() is in play these are exact copies of the stored
    // producer lists, so behavior is unchanged.
    std::map<FuncContents *, std::vector<std::shared_ptr<FuncContents>>> resolved_producers_;
    std::map<std::pair<FuncContents *, int>, std::vector<std::shared_ptr<FuncContents>>>
        resolved_stage_producers_;

    // For consumer `c`, return f' = the wrapper/clone that `c` should read in
    // place of producer `f`, or `f` itself if no redirection applies. A
    // per-consumer wrapper (keyed on the direct-caller `c`) wins (CUSTOM TAKES
    // PRECEDENCE, loopdoc.md section 13); otherwise a global f.in() wrapper
    // redirects `c` -- EXCEPT `f`'s own wrappers, which keep reading `f`. A
    // wrapper (custom or global) is created precisely to read `f`, so the global
    // redirect never applies to any of `f`'s own wrappers: they sit as siblings
    // all consuming `f` (loopdoc.md section 13, in_custom_and_global.cpp).
    static const std::shared_ptr<FuncContents> &redirect(
        const std::shared_ptr<FuncContents> &f, FuncContents *c)
    {
        auto it = f->wrappers.find(c);
        if (it != f->wrappers.end())
        {
            return it->second;
        }
        if (f->global_wrapper && !is_own_wrapper(f, c))
        {
            return f->global_wrapper;
        }
        return f;
    }

    // Is consumer `c` one of `f`'s OWN wrappers (a custom wrapper recorded in
    // f->wrappers, or f's global wrapper)? Such a wrapper reads `f` directly and
    // must NOT be redirected by f's global wrapper (loopdoc.md section 13).
    static bool is_own_wrapper(const std::shared_ptr<FuncContents> &f, FuncContents *c)
    {
        if (f->global_wrapper && f->global_wrapper.get() == c)
        {
            return true;
        }
        for (auto &kv : f->wrappers)
        {
            if (kv.second.get() == c)
            {
                return true;
            }
        }
        return false;
    }

    // One-time pass (loopdoc.md section 13 "Implementation note"): rewrite every
    // Func's / stage's producer list, swapping any producer that has a
    // wrapper/clone registered for this consumer. Fills the backing store the
    // two accessors below read from. Called once at the start of print(); see
    // the RESOLUTION TIMING note above for why this is build-time, not at in()
    // time (a global f.in() may redirect consumers defined after the call).
    //
    // Reachability must be taken over the RESOLVED graph (a wrapper/clone is
    // reachable from the output only through the consumers redirected to it), so
    // we walk from the output, resolving each node's producers as we discover it
    // and following the resolved edges to find the rest. One pass, stable refs.
    // Collect every Func reachable from the output through the call graph
    // as-written AND through any registered wrapper/clone (their pins and the
    // wrapper Funcs themselves), so the pin re-check below can see wrapped Funcs
    // even when the wrapper is unused / unreachable in the resolved graph.
    void collect_all_funcs(FuncContents *f, std::set<FuncContents *> &all)
    {
        if (!f || !all.insert(f).second)
        {
            return;
        }
        for (auto &p : all_producers(f))
        {
            collect_all_funcs(p.get(), all);
        }
        for (auto &kv : f->wrappers)
        {
            collect_all_funcs(kv.first, all);       // the pinned consumer
            collect_all_funcs(kv.second.get(), all); // the wrapper/clone
        }
        if (f->global_wrapper)
        {
            collect_all_funcs(f->global_wrapper.get(), all);
        }
    }

    // Lowering re-check (loopdoc.md section 13 "pins freeze at call time"): an
    // in()/clone_in() pin is resolved and frozen when the call is made, but a
    // LATER eager rewrite (e.g. rfactor, §12) can move the pinned Func's read of
    // the wrapped Func f into a new intermediate -- or the pin may have fallen
    // back to a consumer that never called f. Either way the pinned Func no
    // longer (or never did) directly call f, which Halide rejects:
    //   Cannot wrap "f" in "g" because "g" does not call "f"
    // We reproduce that rejection here. "Calls f" means some STAGE of the pinned
    // Func directly reads f in the CURRENT graph (checked before wrapper
    // resolution, so we see the raw, post-rewrite producer lists).
    void validate_wrapper_pins(FuncContents *output)
    {
        std::set<FuncContents *> all;
        collect_all_funcs(output, all);
        for (FuncContents *f : all)
        {
            for (auto &kv : f->wrappers)
            {
                FuncContents *caller = kv.first;
                bool calls_f = false;
                for (auto &p : all_producers(caller))
                {
                    if (p.get() == f)
                    {
                        calls_f = true;
                        break;
                    }
                }
                if (!calls_f)
                {
                    throw CompileError(
                        "micro_halide: Cannot wrap \"" + f->name + "\" in \"" +
                        caller->name + "\" because \"" + caller->name +
                        "\" does not call \"" + f->name +
                        "\" (loopdoc.md section 13)");
                }
            }
        }
    }

    void resolve_wrappers(FuncContents *output)
    {
        std::vector<FuncContents *> stack{output};
        std::set<FuncContents *> done;
        while (!stack.empty())
        {
            FuncContents *c = stack.back();
            stack.pop_back();
            if (!done.insert(c).second)
            {
                continue;
            }
            std::vector<std::shared_ptr<FuncContents>> rp;
            for (auto &p : all_producers(c))
            {
                const std::shared_ptr<FuncContents> &r = redirect(p, c);
                rp.push_back(r);
                stack.push_back(r.get());
            }
            resolved_producers_[c] = std::move(rp);
            for (int s = 0; s < (int)c->stages.size(); s++)
            {
                std::vector<std::shared_ptr<FuncContents>> rsp;
                for (auto &p : c->stages[s].producers)
                {
                    const std::shared_ptr<FuncContents> &r = redirect(p, c);
                    rsp.push_back(r);
                    stack.push_back(r.get());
                }
                resolved_stage_producers_[{c, s}] = std::move(rsp);
            }
        }
    }

    const std::vector<std::shared_ptr<FuncContents>> &func_producers(FuncContents *f)
    {
        auto it = resolved_producers_.find(f);
        if (it != resolved_producers_.end())
        {
            return it->second;
        }
        // Not visited by resolve_wrappers (unreachable in the resolved graph): no
        // redirection applies, so the raw producer set (from the stages) stands.
        return resolved_producers_[f] = all_producers(f);
    }

    const std::vector<std::shared_ptr<FuncContents>> &stage_producers(FuncContents *f, int s)
    {
        auto it = resolved_stage_producers_.find({f, s});
        return it != resolved_stage_producers_.end() ? it->second : f->stages[s].producers;
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

    // The stage of site_func g whose dimension list contains `var`. compute_at/store_at
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
    // or -1. (Used by store/hoist legality, whose site_funcs are pure Funcs.)
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
        for (auto &p : func_producers(b))
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

    // Does the loop body at site (site_func, hs, var) USE f -- directly, or
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
    // A producer g is "realized in the body at (site_func, hs, var)" iff g is computed
    // at this site_func stage's `var` loop or an INNER one (g's body is then nested
    // inside this loop). g at an OUTER loop of the site_func is NOT in this body --
    // this site lives in g's `consume`, after g (the neg_transitive_..._inner
    // case): such a g does not pull f in here.
    // Guard against mutual recursion: two producers g, h both filed at (site_func, v)
    // can each ask whether the other is present in site_func stage hs ("is g in this
    // body?" -> "is h in this body?" -> ...). The "is X present in this stage"
    // question is monotone, so treating an in-progress query as "not yet known to
    // be present" (false) is the correct fixpoint and breaks the cycle.
    std::set<std::tuple<FuncContents *, int, FuncContents *>> body_uses_active_;

    bool body_uses(FuncContents *site_func, int hs, const std::string &var, FuncContents *f,
                   const std::vector<FuncContents *> &order)
    {
        if (stage_reads(site_func, hs, f))
        {
            return true;
        }
        int ivar = stage_dim_index(site_func, hs, var);
        if (ivar < 0)
        {
            return false;
        }
        auto key = std::make_tuple(site_func, hs, f);
        if (!body_uses_active_.insert(key).second)
        {
            // Already evaluating this exact (site_func, stage, f) query higher in the
            // recursion -- treat as not (yet) present to terminate the cycle.
            return false;
        }
        bool result = false;
        for (FuncContents *g : order)
        {
            if (g == f || g == site_func || !is_realized(g))
            {
                continue;
            }
            // Where is g realized inside site_func's stage hs? Two ways it can be
            // in this body: (a) g is compute_at (site_func, g->at_var); or (b) g
            // is a non-pure inline Func, hence REALIZED at site_func's innermost
            // use of it (loopdoc.md sections 7 + 11) -- but only when site_func's
            // stage hs actually reads g directly (only then is g materialized in
            // this stage's body, at its innermost loop).
            std::string g_var;
            if (g->level == FuncContents::Level::At && g->at_func.get() == site_func)
            {
                g_var = g->at_var;
            }
            else if (g->level == FuncContents::Level::Inline &&
                     stage_reads(site_func, hs, g))
            {
                const std::vector<DimData> &gd = stage_dims(site_func, hs);
                if (gd.empty())
                {
                    continue;
                }
                g_var = gd[0].name(); // innermost use
            }
            else
            {
                continue;
            }
            // g must be realized in THIS stage's body, at var or an inner loop.
            int ig = stage_dim_index(site_func, hs, g_var);
            if (ig < 0 || ig > ivar)
            {
                continue;
            }
            // ...AND g must actually be injected INTO this site_func stage. The level
            // (site_func, g->at_var) names a g->at_var loop in EVERY stage of site_func, but
            // g lands only in the stages whose body uses g (loopdoc.md section 7:
            // "an intermediate g is realized in a stage's body only if THAT stage
            // actually uses g"). So the pull-in stacks per stage: f appears in
            // stage hs iff hs's body uses g at the var loop AND g uses f. Without
            // this check the rfactor intermediate's pure stage (which reads
            // neither g nor f) would wrongly pull f in just because it shares the
            // var loop with the reducing stage (rfactor_indirect_at_intm).
            if (!body_uses(site_func, hs, g_var, g, order))
            {
                continue;
            }
            // Does g (transitively) use f? g's body is its own loop nest; f's use
            // through g is wherever g reads f, which is enclosed by g's loops --
            // and g is in this site_func body, so f's use is too.
            if (g_uses_f(g, f, order))
            {
                result = true;
                break;
            }
        }
        // Post-fusion: site_func may be a member of a fused group, so the loop
        // body at (site_func, hs, var) also contains the bodies of group members
        // spliced into the shared nest at-or-below this loop (loopdoc.md section
        // 14 "Loop ownership"). A member m (other than site_func) whose stage ms
        // is enclosed by (site_func, var) contributes its uses of f to this body.
        // This is why a producer computed at the spine owner's OUTER loop is
        // realized there even though the spine owner's own body never reads it --
        // a fused member nested inside does (human_compute_at_compute_with_child_no:
        // g.compute_at(parent, z), g read only by the fused child).
        if (!result)
        {
            std::shared_ptr<FuseGroup> grp = group_of(site_func);
            if (grp)
            {
                for (FuncContents *m : grp->members)
                {
                    if (m == site_func)
                    {
                        continue;
                    }
                    for (int ms = 0; ms < num_stages(m) && !result; ms++)
                    {
                        if (site_encloses_use(site_func, hs, var, m, ms) &&
                            stage_uses_f(m, ms, f, order))
                        {
                            result = true;
                        }
                    }
                    if (result)
                    {
                        break;
                    }
                }
            }
        }
        body_uses_active_.erase(key);
        return result;
    }

    // Does stage ms of member m use f -- directly (reads f, incl. through inlined
    // producers) or transitively through a producer realized inside m's stage ms
    // body? (Per-stage version of g_uses_f, for the fused-member clause above.)
    bool stage_uses_f(FuncContents *m, int ms, FuncContents *f,
                      const std::vector<FuncContents *> &order)
    {
        if (stage_reads(m, ms, f))
        {
            return true;
        }
        for (const DimData &dv : stage_dims(m, ms))
        {
            if (body_uses(m, ms, dv.name(), f, order))
            {
                return true;
            }
        }
        return false;
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
            // The site_func reads f within its own body, which runs inside this loop.
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
    // A "site" is a loop level (g, gs, gv): site_func g's stage gs, loop named gv. A
    // producer computed there can only feed uses that this loop encloses.

    // Does loop (g, gs, gv) enclose (== is the same as, or outer to) loop
    // (h, hs, hv)?
    bool site_encloses_loop(FuncContents *g, int gs, const std::string &gv,
                            FuncContents *h, int hs, const std::string &hv)
    {
        // A non-pure inline func is realized at each consumer's innermost use
        // (loopdoc.md sections 7 + 11); its own loop (h, hs, hv) nests inside
        // those sites, so enclosure reduces to enclosing every realization site.
        if (h != g && is_realized(h) && h->level == FuncContents::Level::Inline &&
            !(hs < num_stages(h) && h->stages[hs].has_fuse))
        {
            return realized_inline_enclosed(g, gs, gv, h);
        }
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
        // h's stage hs is FUSED into a parent stage (loopdoc.md section 14):
        // h shares the parent's loops from outermost down to the fuse var, so
        // h's loops at/above the fuse level ARE the parent's loops. (g, gv)
        // encloses (h, hs, hv) iff it encloses the parent's fuse loop (or outer).
        // Checked BEFORE h's own compute level: a fused stage defers to its
        // parent within the shared nest, and only the unfused spine owner (and
        // unfused stages generally) reaches the group's compute level via its own
        // At-level. A member can be both fused AND compute_at the group level, so
        // this ordering matters (cwtest_nested_compute_with).
        if (hs < num_stages(h) && h->stages[hs].has_fuse && h->stages[hs].fuse_parent)
        {
            const StageData &sd = h->stages[hs];
            return site_encloses_loop(g, gs, gv, sd.fuse_parent.get(),
                                      sd.fuse_parent_stage, sd.fuse_var);
        }
        // h is realized at its own compute level; follow it outward.
        if (h->level == FuncContents::Level::At)
        {
            FuncContents *site_func = h->at_func.get();
            int site_func_s = resolve_stage(site_func, h->at_var);
            if (site_func_s < 0)
            {
                return false;
            }
            return site_encloses_loop(g, gs, gv, site_func, site_func_s, h->at_var);
        }
        return false; // h at root and not g: not inside g's loop
    }

    // The realization order (set in print() before validation/filing). Used by
    // the realized-inline enclosure below, which must enumerate a Func's readers.
    const std::vector<FuncContents *> *funcs_ = nullptr;

    // A non-pure Func left at the inline level is REALIZED at the innermost use
    // in EACH of its consumers (loopdoc.md sections 7 + 11) -- its body, and any
    // read of f inside it, runs at those sites, NOT at h's own (inline) compute
    // level. So site (g, gs, gv) encloses h's use of f iff it encloses h's
    // realization inside every Func that reads h. This is the §7 rule "a read of
    // f is any read in the realized loop nest, reached through the site func's
    // callees": f reaches the nest through the realized intermediate h, so the
    // site must enclose wherever h itself lands.
    bool realized_inline_enclosed(FuncContents *g, int gs, const std::string &gv,
                                  FuncContents *h)
    {
        if (!funcs_)
        {
            return false;
        }
        bool found = false;
        for (FuncContents *c : *funcs_)
        {
            if (c == h || !is_realized(c))
            {
                continue;
            }
            for (int cs = 0; cs < num_stages(c); cs++)
            {
                if (!stage_reads(c, cs, h))
                {
                    continue;
                }
                found = true;
                const std::vector<DimData> &d = stage_dims(c, cs);
                // h is materialized just inside c's innermost loop (dim0) of
                // stage cs (loopdoc.md section 11); a loop-less stage realizes h
                // at c's leaf, so the whole stage-cs body must be enclosed.
                if (d.empty())
                {
                    if (!site_encloses_use(g, gs, gv, c, cs))
                    {
                        return false;
                    }
                }
                else if (!site_encloses_loop(g, gs, gv, c, cs, d[0].name()))
                {
                    return false;
                }
            }
        }
        return found;
    }

    // Does site (g, gs, gv) enclose the body of stage hs of reader h (i.e. the
    // point where h reads f)?
    bool site_encloses_use(FuncContents *g, int gs, const std::string &gv,
                           FuncContents *h, int hs)
    {
        // A non-pure inline reader is realized at each of its consumers' uses,
        // not at its own (inline) level (loopdoc.md sections 7 + 11).
        if (h != g && is_realized(h) && h->level == FuncContents::Level::Inline &&
            !(hs < num_stages(h) && h->stages[hs].has_fuse))
        {
            return realized_inline_enclosed(g, gs, gv, h);
        }
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
        // h's stage hs is FUSED into a parent stage (loopdoc.md section 14):
        // its body runs inside the parent's shared loops down to the fuse var.
        // The use is enclosed by (g, gv) iff (g, gv) encloses that fuse loop.
        // Checked BEFORE h's own compute level, since a member can be both fused
        // AND compute_at the group level (cwtest_nested_compute_with).
        if (hs < num_stages(h) && h->stages[hs].has_fuse && h->stages[hs].fuse_parent)
        {
            const StageData &sd = h->stages[hs];
            return site_encloses_loop(g, gs, gv, sd.fuse_parent.get(),
                                      sd.fuse_parent_stage, sd.fuse_var);
        }
        if (h->level == FuncContents::Level::At)
        {
            FuncContents *site_func = h->at_func.get();
            int site_func_s = resolve_stage(site_func, h->at_var);
            if (site_func_s < 0)
            {
                return false;
            }
            // h's stage hs runs just inside h's compute loop (site_func, site_func_s,
            // at_var); enclosed iff the site is that loop or outer to it.
            return site_encloses_loop(g, gs, gv, site_func, site_func_s, h->at_var);
        }
        return false; // h at root and not g
    }

    [[noreturn]] static void fail(FuncContents *f, const std::string &why)
    {
        throw CompileError("micro_halide: invalid schedule for Func \"" + f->name +
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
                        fail(f, "store_at site_func is inlined/undefined, so it has no loop");
                    }
                    if (dim_index(sg, sv) < 0)
                    {
                        fail(f, "store_at loop variable does not exist in the site_func Func");
                    }
                    // The store level must ENCLOSE the compute level: same loop
                    // or an outer one.
                    if (f->level == FuncContents::Level::At)
                    {
                        FuncContents *cg = f->at_func.get();
                        bool ok;
                        if (cg == sg)
                        {
                            // Same site_func: store var must be the same loop or an
                            // outer one (outer = higher dim index; arg0 = inner).
                            ok = dim_index(sg, sv) >= dim_index(cg, f->at_var);
                        }
                        else
                        {
                            // Different site_func: the compute site_func's loop (cg, at_var)
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
                        fail(f, "hoist_storage site_func is inlined/undefined, so it has no loop");
                    }
                    if (dim_index(hg, hv) < 0)
                    {
                        fail(f, "hoist_storage loop variable does not exist in the site_func Func");
                    }
                    // The hoist-storage level must ENCLOSE the store level, which
                    // in turn encloses the compute level. The effective store
                    // level is the explicit store_at if present, else the compute
                    // level. We require the hoist loop to be the same loop or an
                    // outer one relative to that effective store level.
                    bool store_is_root = f->has_store_level && f->store_is_root;
                    FuncContents *eff_site_func;
                    std::string eff_var;
                    if (f->has_store_level && !f->store_is_root)
                    {
                        eff_site_func = f->store_func.get();
                        eff_var = f->store_var;
                    }
                    else if (f->level == FuncContents::Level::At)
                    {
                        eff_site_func = f->at_func.get();
                        eff_var = f->at_var;
                    }
                    else
                    {
                        // compute_root with no explicit store_at: effective store
                        // level is root, which only a root hoist level encloses.
                        eff_site_func = nullptr;
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
                    else if (eff_site_func == hg)
                    {
                        // Same site_func: hoist var must be the same loop or an outer
                        // one (outer = higher dim index; arg0 = inner).
                        ok = dim_index(hg, hv) >= dim_index(eff_site_func, eff_var);
                    }
                    else
                    {
                        // Different site_func: the store site_func's loop (eff_site_func, eff_var)
                        // must itself sit inside the hoist loop (hg, hv).
                        ok = enclosed_by(eff_site_func, hg, hv);
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

            // The site_func must itself be computed (have a loop nest).
            if (!g || !is_realized(g))
            {
                fail(f, "compute_at site_func is inlined/undefined, so it has no loop to compute at");
            }
            // The named loop must exist as a dimension of SOME stage of the site_func
            // (loopdoc.md section 10: an RVar loop lives only in its own stage,
            // but is still a valid compute_at site).
            int gs = resolve_stage(g, v);
            if (gs < 0)
            {
                fail(f, "compute_at loop variable does not exist in the site_func Func");
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
                        std::string stage_name = h->name;
                        if (hs > 0) {
                            stage_name += ".update(";
                            stage_name += std::to_string(hs);
                            stage_name += ")";
                        }
                        // Human: I added h and hs to the message.
                        fail(f, "it is read by stage " + stage_name + " that is not "
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
        // store_at(g, v): equal to compute level iff same site_func and same var.
        if (f->level == FuncContents::Level::At && f->at_func.get() == f->store_func.get() &&
            f->at_var == f->store_var)
        {
            return false;
        }
        return true;
    }

    // A realized ITEM (loopdoc.md section 15): a single Func, or a whole fused
    // group emitted as one unit. The list machinery below operates on items so a
    // group's members get one shared interleaved body wrapped by every member's
    // produce/consume.
    struct Item
    {
        FuncContents *single = nullptr; // non-null for a lone Func
        FuseGroup *group = nullptr;     // non-null for a fused group
    };

    // Collapse a realization-order list of Funcs into a list of items: each
    // fused group appears once (as a single node), all its members dropped from
    // their individual slots; lone Funcs pass through (loopdoc.md section 15 step
    // 2 + 4). The group node is placed at the position of its LAST member in
    // `funcs`, so that an external producer feeding one member but interleaved
    // between members in the flat realization order (e.g. f1 between the two
    // rfactor intermediates in cwtest_update_stage_rfactor) is emitted BEFORE the
    // whole group -- the group is one node in realization order, after all its
    // producers. (For contiguous members first == last position, so this only
    // changes the interleaved case.)
    std::vector<Item> collapse_to_items(const std::vector<FuncContents *> &funcs)
    {
        // Last index at which each group's member occurs.
        std::map<FuseGroup *, size_t> last_index;
        for (size_t i = 0; i < funcs.size(); i++)
        {
            std::shared_ptr<FuseGroup> grp = group_of(funcs[i]);
            if (grp)
            {
                last_index[grp.get()] = i;
            }
        }
        std::vector<Item> items;
        for (size_t i = 0; i < funcs.size(); i++)
        {
            std::shared_ptr<FuseGroup> grp = group_of(funcs[i]);
            if (grp)
            {
                // Emit the group node once, at its last member's slot.
                if (last_index[grp.get()] == i)
                {
                    Item it;
                    it.group = grp.get();
                    items.push_back(it);
                }
            }
            else
            {
                Item it;
                it.single = funcs[i];
                items.push_back(it);
            }
        }
        return items;
    }

    // Emit the items in `items` as a chain of produce/consume blocks. `cont` (if
    // present) is the continuation that follows the last item and is wrapped by
    // its consume block(s).
    template <typename Cont>
    void emit_items(const std::vector<Item> &items, size_t i, int indent,
                    const Cont &cont, bool has_cont)
    {
        if (i >= items.size())
        {
            if (has_cont)
            {
                cont(indent);
            }
            return;
        }
        bool more = (i + 1 < items.size()) || has_cont;
        auto rest = [this, &items, i, &cont, has_cont](int ind) {
            emit_items(items, i + 1, ind, cont, has_cont);
        };
        const Item &it = items[i];
        if (it.group)
        {
            // produce for every member (last-realized / spine owner outermost),
            // then the interleaved group body, then a mirrored consume stack
            // wrapping the rest (loopdoc.md section 14).
            const std::vector<FuncContents *> &po = it.group->produce_order;
            int ind = indent;
            for (FuncContents *m : po)
            {
                out << pad(ind) << "produce " << m->name << ":\n";
                ind += 2;
            }
            emit_group_body(it.group, ind);
            if (more)
            {
                int cind = indent;
                for (FuncContents *m : po)
                {
                    out << pad(cind) << "consume " << m->name << ":\n";
                    cind += 2;
                }
                rest(cind);
            }
        }
        else
        {
            FuncContents *f = it.single;
            out << pad(indent) << "produce " << f->name << ":\n";
            emit_func_loops(f, indent + 2);
            if (more)
            {
                out << pad(indent) << "consume " << f->name << ":\n";
                rest(indent + 2);
            }
        }
    }

    // Backward-compatible shim: emit a list of Funcs (collapsing any fused
    // groups into single items) as a produce/consume chain.
    template <typename Cont>
    void emit_realizations(const std::vector<FuncContents *> &funcs, size_t i,
                           int indent, const Cont &cont, bool has_cont)
    {
        // `i` is always 0 at the call sites; collapse then emit.
        std::vector<Item> items = collapse_to_items(funcs);
        emit_items(items, i, indent, cont, has_cont);
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
            // A stage's specialization list (loopdoc.md section 15) lowers to
            // one loop nest per branch, emitted back to back inside the single
            // `produce`: branches in declaration order, then this stage's own
            // dims as the fallback last (dropped by specialize_fail). flatten
            // walks the branch TREE, so a nested specialization contributes its
            // own branches before its fallback. With no specializations this is
            // just the stage itself, so unspecialized Funcs are unchanged.
            std::vector<const StageData *> nests;
            flatten_specializations(&f->stages[s], nests);
            for (const StageData *sd : nests)
            {
                emit_dim(f, s, *sd, (int)sd->dims.size() - 1, indent);
            }
        }
    }

    // Flatten a stage's specialization tree (loopdoc.md section 15) into the
    // ordered list of schedules to emit: for each specialization, recurse into
    // its branch (nested branches expand depth-first), then append THIS node's
    // own dims as the fallback -- unless specialize_fail dropped it.
    void flatten_specializations(const StageData *sd, std::vector<const StageData *> &out)
    {
        for (const Specialization &sp : sd->specializations)
        {
            flatten_specializations(sp.schedule.get(), out);
        }
        if (!sd->specialize_failed)
        {
            out.push_back(sd);
        }
    }

    // Emit one loop nest for stage `stage` of `f`, walking the dimension list of
    // schedule `sd` (a base stage, or one specialization branch's forked copy --
    // loopdoc.md section 15). Child-injection and store-node lookups stay keyed
    // by the BASE (f, stage) so a compute_at producer filed at this stage is
    // injected into EACH branch, resolved against that branch's own dims (each
    // branch inherits the same loop names, so the by-name lookup lands correctly).
    void emit_dim(FuncContents *f, int stage, const StageData &sd, int dim, int indent)
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
        const std::string &var = sd.dims[dim].name();

        // An elided ("collapsed") loop prints no `for` line and does not
        // indent its body, but is still a valid injection site for any
        // compute_at children filed at this level (see the "compute_at at an
        // elided loop level" example).
        bool elided = sd.collapsed.count(var) != 0;
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

        auto deeper = [this, f, stage, &sd, dim](int ind) { emit_dim(f, stage, sd, dim - 1, ind); };
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

        // Reject stale / no-path in()/clone_in() pins before resolving them
        // (loopdoc.md section 13 "pins freeze at call time"): a pinned Func that
        // no longer calls the wrapped Func is a lowering-time error in Halide.
        validate_wrapper_pins(output);

        // Resolve in()/clone_in() wrapper redirection ONCE, up front, before any
        // producer is read (loopdoc.md section 13 "Implementation note"). All
        // later producer reads go through func_producers/stage_producers, which
        // return from the backing store this fills.
        resolve_wrappers(output);

        // Establish first-visitation order (the tie-breaker key).
        std::set<FuncContents *> visit_seen;
        std::uint64_t counter = 0;
        compute_visit_order(output, visit_seen, counter);

        // Build fused groups (loopdoc.md section 14) BEFORE the realization walk:
        // a group is a single contracted vertex in the realization graph
        // (loopdoc.md section 6), so realization_order needs group membership. Any
        // ordering of the reachable set works -- build_groups re-sorts members
        // itself.
        std::vector<FuncContents *> reachable(visit_seen.begin(), visit_seen.end());
        build_groups(reachable);

        // Realization order: post-order DFS with fused groups contracted.
        std::set<FuncContents *> visited;
        std::vector<FuncContents *> order;
        realization_order(output, visited, order);
        funcs_ = &order; // reader enumeration for realized-inline enclosure

        // Reject illegal schedules before emitting (mirrors Halide aborting).
        validate_groups(order);
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
                // (site_func, var) denotes the `var` loop in EVERY stage of the site_func
                // (loopdoc.md section 7): f is injected just inside that loop in
                // each stage of the site_func whose body at that level USES f, and
                // stages that do not use f get nothing. "Uses" is transitive: the
                // body uses f directly (the stage reads f) OR through another
                // producer realized in that body that itself uses f (loopdoc.md
                // section 7, "Computing at an indirect consumer's loop"). So a
                // producer read by several stages lands once per using stage, and
                // an indirect producer lands wherever its consumer chain is
                // realized. (For an RVar site only one stage has the loop, so this
                // reduces to a single injection.)
                FuncContents *site_func = f->at_func.get();
                for (int hs = 0; hs < num_stages(site_func); hs++)
                {
                    if (stage_dim_index(site_func, hs, f->at_var) >= 0 &&
                        body_uses(site_func, hs, f->at_var, f, order))
                    {
                        children_at[{site_func, hs, f->at_var}].push_back(f);
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
                    // The store node follows f PER HOST STAGE, just as the
                    // produce/consume does (loopdoc.md section 8). The level
                    // (store_site_func, store_var) names a store_var loop in EVERY
                    // stage of the site_func, but f is computed only in the site_func
                    // stages whose body USES f -- so the `store f:` node appears
                    // at store_var in exactly those stages, never in a site_func stage
                    // that merely has the loop but never computes f. This mirrors
                    // the per-stage compute-injection above (body_uses), instead
                    // of a single resolve_stage that would wrongly pin the store
                    // node to the lowest-index stage having the loop (e.g. a pure
                    // stage that does not read f).
                    FuncContents *sh = f->store_func.get();
                    for (int hs = 0; hs < num_stages(sh); hs++)
                    {
                        if (stage_dim_index(sh, hs, f->store_var) >= 0 &&
                            body_uses(sh, hs, f->store_var, f, order))
                        {
                            store_at_level[{sh, hs, f->store_var}].push_back(f);
                        }
                    }
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
