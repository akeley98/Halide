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
