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
class Expr
{
  public:
    // Funcs this expression reads from (may contain duplicates; deduped later).
    std::vector<std::shared_ptr<FuncContents>> deps;

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
    // Reading a Func (a FuncRef) inside an expression adds it as a producer.
    Expr(const FuncRef &);
};

inline Expr combine(const Expr &a, const Expr &b)
{
    Expr r;
    r.deps = a.deps;
    r.deps.insert(r.deps.end(), b.deps.begin(), b.deps.end());
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

    // Names of this Func's loop variables that Halide elides because their
    // required extent is provably 1 (a "point loop"). See `micro_halide_collapses`
    // below and loopdoc.md: this is *declared* per example (it depends on bounds
    // inference, which is out of scope), not derived. An elided loop drops its
    // `for` line but is still a valid injection site for compute_at children.
    std::set<std::string> collapsed;
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

    // Define the Func: f(x, y, ...) = rhs;
    void operator=(const Expr &rhs)
    {
        func->args = vars;
        // Dependencies are the funcs read by the RHS plus any read in the
        // index expressions, deduplicated by identity.
        std::vector<std::shared_ptr<FuncContents>> deps = rhs.deps;
        for (const Expr &a : arg_exprs)
        {
            deps.insert(deps.end(), a.deps.begin(), a.deps.end());
        }
        std::set<FuncContents *> seen;
        func->producers.clear();
        for (auto &d : deps)
        {
            if (d && seen.insert(d.get()).second)
            {
                func->producers.push_back(d);
            }
        }
        func->defined = true;
    }
    void operator=(const FuncRef &rhs)
    {
        *this = Expr(rhs);
    }
};

inline Expr::Expr(const FuncRef &ref)
{
    deps.push_back(ref.func);
    for (const Expr &a : ref.arg_exprs)
    {
        deps.insert(deps.end(), a.deps.begin(), a.deps.end());
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

    // Reading an ImageParam is not a Func dependency (it is already stored).
    template <typename... Args>
    Expr operator()(Args...) const
    {
        return Expr();
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

    void print_loop_nest();
};

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
        emit_realizations(kids, 0, body_indent, deeper, /*has_cont=*/true);
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
        }

        auto no_cont = [](int) {};
        emit_realizations(root_list, 0, 0, no_cont, /*has_cont=*/false);
    }
};

} // namespace internal

inline void Func::print_loop_nest()
{
    internal::LoopNestPrinter printer(std::cerr);
    printer.print(contents.get());
}

} // namespace micro_halide
