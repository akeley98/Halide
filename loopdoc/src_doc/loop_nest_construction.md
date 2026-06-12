# Source evidence: loop-nest construction (bootstrap subset)

This file backs the claims in [../loopdoc.md](../loopdoc.md) §§2–10 with
citations into the Halide compiler. Paths are relative to the Halide source
root (`../src` from the loopdoc directory). Line numbers are approximate and
may drift; the surrounding function names are the stable anchors.

The entry point for the loop-nest pseudocode is
`Internal::print_loop_nest(const vector<Function> &)` in
`src/PrintLoopNest.cpp`. It runs the front of the normal lowering pipeline and
then walks the resulting IR `Stmt` with a small `IRVisitor`.

## 1. The output Func is forced to root

`print_loop_nest` explicitly schedules every output Func at root before
lowering:

    // src/PrintLoopNest.cpp, print_loop_nest()
    for (const Function &f : outputs) {
        Func(f).compute_root().store_root();
    }

This is why the output never inlines and always appears as the outermost
`produce` (loopdoc §4, §5, §10 step 1). In the real `realize`/`compile` path the
same effect comes from the output simply being the realized buffer.

## 2. The default schedule is "inlined"

A Func's compute and store levels default to `LoopLevel::inlined()`:

    // src/Schedule.cpp, FuncScheduleContents ctor (~line 249)
    : store_level(LoopLevel::inlined()),
      compute_level(LoopLevel::inlined()),
      hoist_storage_level(LoopLevel::inlined()) { ... }

`schedule_functions` (in `src/ScheduleFunctions.cpp`) treats an inlined Func by
substituting its definition into its callers rather than giving it a loop nest;
`is_inlined()` / `is_root()` are checked throughout (e.g. the
`InjectRealization`/inlining logic gated on
`level.is_inlined() || level.is_root()` near line 1003, and the inlined branch
at ~1977 / 2298). This backs loopdoc §4: unscheduled Funcs vanish from the nest.

## 3. Realization order

The topological producers-before-consumers order is computed by

    // src/RealizationOrder.cpp
    pair<vector<string>, vector<vector<string>>>
    realization_order(const vector<Function> &outputs,
                      map<string, Function> &env);

`print_loop_nest` calls it (`auto [order, fused_groups] =
realization_order(outputs, env);`) and `schedule_functions` injects each
function's realization in that order. This backs loopdoc §5 ("realization
order") and §10 step 2. Inlined Funcs remain in `env` and so still contribute
edges to the ordering even though they get no realization.

### Sibling tie-break

`realization_order` builds, for each Func, the list of its direct callees, then
runs a post-order DFS (`realization_order_dfs`). The *order siblings come out
in* is fixed by sorting each callee list before the DFS:

    // src/RealizationOrder.cpp, sort_funcs_by_name_and_counter (~256)
    // sort key per Func: (prefix, visitation_counter, full_name)
    string prefix = split_string(full_name, "$")[0];
    while (!prefix.empty() && std::isdigit(prefix.back())) prefix.pop_back();
    ...
    std::sort(items.begin(), items.end());   // tuple<prefix, counter, name>

The comment there explains the intent: make the order resistant to
`unique_name` churn so that machine-generated schedules stay valid. The
`counter` is the Func's first-visitation index, from

    // src/RealizationOrder.cpp
    map<string, uint64_t> compute_visitation_order(const vector<Function> &outputs) {
        vector<Function> funcs = called_funcs_in_order_found(outputs);
        ...
    }

and `called_funcs_in_order_found` (`src/FindCalls.cpp`) is a pre-order DFS from
the outputs (`populate_environment_helper` inserts a Func, then recurses into
its calls in IR order). This is the source basis for loopdoc §5's tie-break
rule (prefix, then visitation order, then full name) — and explains why the
left-to-right order of a defining expression does *not* decide which sibling
producer is realized first (see
`examples/tiebreak_realization_order.cpp`). micro_halide mirrors this in
`LoopNestPrinter::sort_key` / `compute_visit_order`.

## 4. produce / consume nesting

The `produce` and `consume` IR nodes are created in `build_realization` /
`InjectFunctionRealization` in `src/ScheduleFunctions.cpp`:

    // src/ScheduleFunctions.cpp (~1866)
    // Add the producer nodes.
    for (const auto &i : funcs) {
        producer = ProducerConsumer::make_produce(i.name(), producer);
    }
    // Add the consumer nodes.
    for (size_t i = 0; i < funcs.size(); i++) {
        if (!is_output_list[i]) {
            consumer = ProducerConsumer::make_consume(funcs[i].name(), consumer);
        }
    }
    if (is_no_op(consumer)) {
        return producer;             // first/output realization: no consume
    } else {
        return Block::make(producer, consumer);
    }

Two facts in loopdoc §5 come directly from here:

* The **output gets no `consume`**: `is_output_list[i]` suppresses it (and for
  the very first realization the consumer is a no-op and is dropped).
* Each realization is `Block(produce, consume)` with the consume wrapping the
  *rest* of the program — which is how the chain in loopdoc §5 nests.

The visitor that prints these nodes:

    // src/PrintLoopNest.cpp, visit(const ProducerConsumer *)
    out << (op->is_producer ? "produce " : "consume ")
        << simplify_func_name(op->name) << ":\n";

## 5. A Func's own loops; first arg is innermost

`build_produce_definition` (`src/ScheduleFunctions.cpp` ~1508) emits the loops
for a definition over its dimensions. The dimension list is ordered with the
pure args such that the *first* argument ends up the innermost loop and the
last the outermost — matching the `for c: for y: for x:` ordering for
`f(x, y, c)` (loopdoc §3). The `For` printer:

    // src/PrintLoopNest.cpp, visit(const For *)
    out << get_indent() << op->for_type << " " << simplify_var_name(op->name);
    // ... prints " in [min, max]" only when both are const ...

and the leaf:

    // src/PrintLoopNest.cpp, visit(const Provide *)
    out << get_indent() << simplify_func_name(op->name) << "(...) = ...\n";

confirms the leaf line shape `f(...) = ...` (loopdoc §2).

## 6. compute_at injection point

For a Func with `compute_level == at(host, var)`, `schedule_functions` finds
the loop in `host` whose name matches the compute level and injects the
producer's realization at that point. The matching is done by the
`compute_level.match(for_loop->name)` test inside the injecting mutator:

    // src/ScheduleFunctions.cpp (~1299)
    if (compute_level.match(for_loop->name)) {
        ...
        _found_compute_level = true;
    }

The realization (`produce`/loops/`consume`) is spliced in as a prefix of that
loop's body, with the remainder of the body becoming the `consume` content.
This backs loopdoc §6's nesting picture and §10 steps 3–4.

### Legality of a compute_at site

Before injecting, `schedule_functions` validates the requested level against the
set of legal sites computed by `ComputeLegalSchedules` (`src/ScheduleFunctions.cpp`).
That visitor walks the loop nest maintaining the current stack of enclosing loop
levels (`sites`) and, at every *use* of the Func, intersects:

    // src/ScheduleFunctions.cpp, ComputeLegalSchedules::register_use (~1936)
    if (!found) { sites_allowed = sites; }      // first use: its enclosing loops
    else {
        // keep only loop levels common to this use and all previous uses
        for (s1 : sites) for (s2 : sites_allowed)
            if (s1.loop_level.match(s2.loop_level)) common_sites.push_back(s1);
        sites_allowed.swap(common_sites);
    }

So `sites_allowed` ends up as the loop levels that **enclose every use** of the
Func (their common ancestors), always including `root`. The requested
`compute_at` is then looked up in that set:

    // src/ScheduleFunctions.cpp (~2333, ~2380)
    for (i : sites) if (sites[i].loop_level.match(compute_at) && ...) compute_idx = i;
    ...
    if (!all_ok()) {
        err << "Func \"" << f.name() << "\" is computed at the following invalid location:\n" ...
            << "Legal locations for this function are:\n" ...   // prints sites_allowed
        user_error << err.str();   // aborts (no exceptions build) / throws CompileError
    }

This is the source basis for loopdoc §6 "When a compute_at is illegal":

* loop does not exist  → requested level matches no `Site` → not found.
* host is not a consumer → the host's loops never appear in any use's stack, so
  they are never in `sites_allowed`.
* a consumer lies outside the site → that consumer's use stack does not contain
  the site, so intersection drops it; with two unrelated uses only `root`
  survives.

micro_halide mirrors this with `LoopNestPrinter::validate` (`enclosed_by` =
"the site is a common ancestor of this use"), throwing instead of printing,
which makes the binary exit non-zero exactly as Halide's `user_error` does.

## 7. Why a compute_at Func can emit fewer loops than it has dimensions

Bounds inference computes, for each realization, the *region* of the Func
required at that point in the nest. A dimension needed at only a single point
yields a loop whose min equals its max. The simplifier then removes such a loop
entirely, replacing it with a `let` that binds the loop variable to the single
value:

    // src/Simplify_Stmts.cpp, Simplify::visit(const For *)  (~282)
    } else if (equal(new_min, new_max) &&
               op->device_api == DeviceAPI::None) {
        // Loop body runs exactly once
        return mutate(LetStmt::make(op->name, new_min, new_body));
    }

`print_loop_nest` runs `simplify(s)` as its last step, so these extent-1 loops
are gone before printing. A root Func is required over its full output region,
so none of its loops collapse; a `compute_at` Func is required only over the
sub-region read per host iteration, so any pointwise dimension collapses. This
is the source-level basis for the caveat in loopdoc §6: the loop *count* of a
`compute_at` Func is a function of bounds inference, not just its
dimensionality.

To see it directly, `HL_DEBUG_CODEGEN=2` dumps the bounds and the pre-simplify
loop nest; compare the `for` over a collapsed dimension (min == max) before
`simplify` with its absence afterwards.

Note the elision is purely a *printing/simplification* effect: the producer's
realization is injected at the loop level during `schedule_functions` (before
simplify), so when the loop is later collapsed into a `LetStmt`, anything that
was injected at that level — including a `compute_at` child of the collapsed
loop — stays at that position; only the `For` node disappears. This is the
source-level basis for "an elided loop is still an injection site" in loopdoc
§8 (see `examples/compute_at_elided_level.cpp`). Because predicting min == max
requires the full bounds model, loopdoc declares elision via the `micro_halide_collapses`
annotation rather than deriving it; that annotation has no counterpart in the
real compiler (it is a no-op shim, `halide_compat/halide_compat.h`).

## 8. store_at / store_root: the `store` node

Backs loopdoc §7.

### Where the storage (Realize) node is injected

`schedule_functions` injects each Func's storage as a `Realize` node at its
**store level**, separately from the `produce`/`consume` (ProducerConsumer) and
loops that go at its compute level. In the For-loop mutator:

    // src/ScheduleFunctions.cpp (~1307)
    if (funcs[i].schedule().store_level().match(for_loop->name)) {
        ...
        body = build_realize_function_from_group(body, i);   // wrap body in Realize
    }

    // build_realize (~1394)
    s = Realize::make(name, func.output_types(), memory_type, bounds, const_true(), s);

So the `Realize` wraps everything from the store-level loop down (including the
host loops between the store and compute levels, and the `produce`/`consume`
spliced in deeper at the compute level). `store_root()` is `LoopLevel::root()`,
the outermost level, so its `Realize` ends up wrapping the whole pipeline body —
this is why loopdoc §7's `store_root` node prints outside the output's
`produce`.

### Why the `store` line appears only when store != compute

The `Realize` node is *always* present (storage must be allocated somewhere),
but `print_loop_nest` only prints a `store` line when the store level differs
from the compute level:

    // src/PrintLoopNest.cpp, visit(const Realize *)
    if (it != env.end() &&
        !(it->second.schedule().store_level() ==
          it->second.schedule().compute_level())) {
        out << "store " << simplify_func_name(op->name) << ":\n";
        indent += 2; op->body.accept(this); indent -= 2;
    } else {
        op->body.accept(this);   // no store line; just recurse
    }

This is the source basis for "the `store` node is shown only when store !=
compute" (loopdoc §7), and for `store_root().compute_root()` printing no store
node (both at root, so equal).

### Legality

`validate_schedule` (~2285) looks up the requested store and compute levels in
the list of legal `Site`s (the enclosing-loop stack intersection from
`ComputeLegalSchedules`, see §6 above) and requires the store site to be found
*before* (i.e. outside) the compute site:

    // src/ScheduleFunctions.cpp (~2333)
    if (sites[i].loop_level.match(store_at) && hoist_storage_idx >= 0) store_idx = i;
    if (sites[i].loop_level.match(compute_at) && store_idx >= 0 ...)   compute_idx = i;
    ...
    if (!all_ok()) user_error << "... is computed at the following invalid location ...";

Because `compute_idx` is only set once `store_idx >= 0`, a store level inside
the compute level can never satisfy both — hence loopdoc §7's "store must
enclose compute" and `neg_store_inside_compute.cpp`. Separately, a store level
on an inlined Func is rejected up front:

    // src/ScheduleFunctions.cpp (~2300)
    user_error << "Func \"" << f.name() << "\" is scheduled store_at(), but is "
               << "inlined. Funcs that use store_at must also call compute_at.\n";

which backs `neg_store_at_inlined.cpp`.

## 9. hoist_storage / hoist_storage_root: invisible to print_loop_nest

Backs loopdoc §7's hoist-storage subsection.

### No print effect

`hoist_storage` introduces a third level (the hoist-storage level) that only
controls where the physical allocation is emitted; it does not trigger sliding
window. `print_loop_nest`'s visitor (`src/PrintLoopNest.cpp`) has handlers only
for `For`, `Realize` (the `store` line, gated on store != compute), 
`ProducerConsumer`, `Provide`, and `LetStmt`. There is no handler keyed on the
hoist-storage level, so changing it does not change the printed nest. (In the
full lowering pipeline the hoist level affects where the allocation is
physically placed, but that is past what `print_loop_nest` shows.) This backs
loopdoc §7: a legal `hoist_storage` schedule prints identically to the same
schedule without it.

### Legality

`validate_schedule` (`src/ScheduleFunctions.cpp` ~2298) rejects `hoist_storage`
on an inlined Func:

    // ~2305
    if (hoist_storage_at.is_root()) {
        user_error << "... scheduled hoist_storage_root(), but is inlined. ...";
    } else if (!hoist_storage_at.is_inlined()) {
        user_error << "... scheduled hoist_storage(), but is inlined. "
                   << "Funcs that use hoist_storage_root must also call compute_at.";
    }

and requires the hoist level to enclose the store level (which encloses
compute), via the same `Site` scan used for store/compute (~2333): the indices
are resolved outermost-first as

    hoist_storage_idx found  ->  then store_idx (requires hoist found)
                             ->  then compute_idx (requires store found)

so a hoist level inside the store/compute level can never satisfy all three and
yields the "invalid location" error. This backs `neg_hoist_at_inlined.cpp` and
`neg_hoist_inside_compute.cpp`. By default the hoist level coincides with the
store level, so an unset hoist level adds no constraint.

## 10. split / fuse / reorder / tile: rewriting the dimension list

Backs loopdoc §8. These directives never touch the producer/consumer graph or
the scheduling *levels*; they only mutate one stage's representation of its own
loops.

### The representation: `dims` and `splits`

A stage's schedule (`src/Schedule.h`) holds two relevant vectors:

    // src/Schedule.h ~446
    struct Dim { std::string var; ForType for_type; ... };   // one entry per loop
    // src/Schedule.h ~332
    struct Split { std::string old_var, outer, inner; Expr factor;
                   enum SplitType { SplitVar, RenameVar, FuseVars } split_type; };

`StageSchedule::dims()` is the ordered loop list, **innermost first**, and always
ends with the `Var::outermost()` sentinel. `splits()` records the split/rename/
fuse operations in application order (used later by bounds inference to relate
the new vars to the original ones). For `print_loop_nest`, what matters is the
`dims` list: `build_provide`/`build_produce_definition` in
`src/ScheduleFunctions.cpp` (see §5 above) emit one `For` per `Dim`, outermost
first (the reverse of `dims`). So the only structural lever the transforms have
is how they edit `dims`.

### split (`Stage::split`, `src/Func.cpp` ~1076)

    // ~1117: find old in dims, then
    dims.insert(dims.begin() + i, dims[i]);   // duplicate the slot
    dims[i].var     = old + "." + inner;      // innermost copy
    dims[i + 1].var = old + "." + outer;      // just outside it

So `old` at position `i` is replaced by two adjacent dims — `inner` at `i`
(innermost), `outer` at `i+1` — and a `Split{old,outer,inner,factor,SplitVar}`
is appended. Net: `dims` grows by one, i.e. one extra `For`. The new names are
the dotted `old.inner` / `old.outer` seen in raw output. Backs `split_basic.cpp`.

### fuse (`Stage::fuse`, `src/Func.cpp` ~1308)

    // ~1331: erase the outer dim
    dims.erase(dims.begin() + i);             // outer removed
    // ~1347: rename the inner dim's slot to the fused name
    dims[i].var = inner + "." + fused;        // fused takes inner's position

`outer` is removed and `inner`'s slot is renamed to the fused var (covering the
product of the two extents); a `Split{..., FuseVars}` is appended. Net: `dims`
shrinks by one, i.e. one fewer `For`. Backs `fuse_basic.cpp`.

### reorder (`Stage::reorder`, `src/Func.cpp` ~1813)

    // ~1822: record each listed var's current position
    for i: idx[i] = position of vars[i] in dims;   // user_error if not found
    // ~1870: place the listed vars into the SORTED set of those positions
    sorted = sort(idx);
    for i: dims[sorted[i]] = dims_old[idx[i]];

So `reorder` permutes **only the slots the listed vars currently occupy** —
unlisted dims keep their positions — filling those slots in the given
innermost-first order. The `user_assert(found)` at ~1831 is the
"could not find var … to reorder" error backing `neg_reorder_bad_var.cpp`;
duplicates are rejected at ~1838. Note `dims` ordering is the *only* thing
reorder changes.

### Why a pure-serial reorder is invisible

The `For` printer (§5) emits `op->for_type` and `simplify_var_name(op->name)`,
and prints `" in [min, max]"` only for constant bounds. The test harness's
`canonicalize.py` then drops the var name entirely and drops constant bounds.
A serial-loop `reorder` changes only names and the order of otherwise-identical
`for` lines, both erased — hence loopdoc §8's "invisible except through a
topological consequence". The consequence is real because `compute_at`
injection (§6 above) matches the *level name* in the post-reorder `dims`: moving
a dim inward/outward moves the loop a producer is filed under, changing how many
host loops land inside its `consume`. Backs `reorder_topological.cpp` vs
`reorder_baseline.cpp`. (A loop-type change — `parallel`/`vectorize`/`unroll`,
which set `Dim::for_type` — *is* kept by both the printer and canonicalizer, so
reordering typed loops would be visible; that is a later milestone.)

### tile (`Stage::tile`, `src/Func.cpp` ~1754)

The two-var `tile` is implemented as two `split`s followed by a `reorder` of the
four resulting vars to `{xi, yi, xo, yo}` (innermost first), exactly as loopdoc
§6 states. Net: `dims` grows by two. Backs `tile_basic.cpp`.

### Sites are matched post-transform

Because `compute_at`/`store_at` resolve their level by matching the name against
the host's `dims` at scheduling time, the transformed vars are the legal sites,
and consumed vars are gone. `g.compute_at(out, x)` after `out.fuse(x, y, xy)`
fails the `ComputeLegalSchedules` lookup (§6 above) since no loop named `x`
remains — only `xy` (and `outermost`/`root`). Backs
`neg_compute_at_fused_away.cpp` and `split_compute_at.cpp`.

## 11. Update (reduction) definitions: stages

Backs loopdoc §4 (stage structure); the cross-stage compute_at parts are §6.

### A Func is a list of stages

`Function` stores one initial `Definition` plus a vector of update
`Definition`s (`src/Function.h`):

    const Definition &definition() const;          // stage 0 (pure)
    const std::vector<Definition> &updates() const; // stages 1..k
    bool has_update_definition() const;

Each `Definition` has its **own** `StageSchedule` (`def.schedule()`) — its own
`dims` list and `splits` — which is why `split`/`reorder`/etc. apply per stage
(`f` schedules `definition()`; `f.update(i)` schedules `updates()[i]`). The
pure stage's `dims` are the pure args; an update stage's `dims` are built at
definition time from the free `Var`s on its left-hand side plus the `RVar`s of
its `ReductionDomain`, with the reduction vars placed innermost.

### All stages share one `produce` (sibling nests, no inner consume)

The injector builds each stage's loop nest separately and concatenates them
into a single producer `Stmt` (`InjectFunctionRealization`, `build_pipeline_group`,
`src/ScheduleFunctions.cpp` ~1808):

    for (const auto &func_stage : stage_order) {
        string def_prefix = f.name() + ".s" + to_string(func_stage.second) + ".";
        const auto &def = (func_stage.second == 0) ? f.definition()
                                                   : f.updates()[func_stage.second - 1];
        Stmt produce_def = build_produce_definition(f, def_prefix, def,
                                                    func_stage.second > 0, ...);
        producer = inject_stmt(producer, produce_def, def.schedule().fuse_level().level);
    }

The combined `producer` is wrapped in exactly one `ProducerConsumer` node
(`build_pipeline_group` -> `ProducerConsumer::make(... is_producer=true ...)`),
which `PrintLoopNest` prints as the single `produce f:`; the stages appear as
consecutive loop nests inside it. There is no `consume` between stages — the
`consume f` (if any) wraps the whole producer, because consumers read the final
post-update buffer. Stages are named `f.s0.`, `f.s1.`, … via `def_prefix`.
Backs `hist_1d`, `sum_reduction`, `two_updates`, `update_2d_rdom`.

### Per-stage scheduling

Because each `def.schedule().dims()` is independent, a transform on
`f.update(0)` rewrites only that update's `dims` (§10 above; `split`/`reorder`
mechanics are §10 of this file applied to the update's list, which may contain
`RVar`s). Backs `update_stage_split`, `update_stage_reorder`. The `RVar`-reorder
legality check that this doc deliberately leaves unmodeled lives in
`Stage::reorder` (`src/Func.cpp` ~1845): it calls `prove_associativity` and
errors if a pair of impure RVars would be swapped in a non-commutative /
non-associative reduction — a semantic test on the update arithmetic, which
`micro_halide`'s dependency-only `Expr` cannot perform.

### Compute level is per-Func; legal sites span all stages

`compute_at`/`store_at` are properties of the `Function` (its `FuncSchedule`),
so the whole multi-stage `producer` is injected at the chosen site
(`func_update_compute_at`). `ComputeLegalSchedules` (§6 above) walks the entire
loop nest, so its `register_use` fires at **every** use across **every** stage;
the legal-site set is the intersection of the enclosing-loop stacks over all of
them. An `RVar` loop appears only inside its own stage's body, so a producer
also used by another stage cannot be computed there. Backs `producer_at_rvar`
(legal: `p` used only in the update's reduction) and
`neg_compute_at_update_rvar` (illegal: `p` used in both the pure and update
stages, so the update's `r` loop does not enclose the pure-stage use).

### What `(g, var)` denotes across stages: the LoopLevel wildcard + use-gating

`f.compute_at(g, var)` builds a `LoopLevel` whose stage index is **left
unspecified** (the public ctor defaults it to `-1`):

    // src/Schedule.h
    LoopLevel(const Func &f, const VarOrRVar &v, int stage_index = -1);

`LoopLevel::match` treats `stage_index == -1` as a **wildcard over all stages**
— it matches the named loop in *every* stage of `g`, because it only checks the
func-name prefix and the var-name suffix (not the `.sN.` stage tag):

    // src/Schedule.cpp, LoopLevel::match(const std::string &loop)
    if (contents->stage_index == -1) {
        return starts_with(loop, func_name + ".") &&   // matches g.s0.var,
               ends_with(loop, "." + var_name);          //   g.s1.var, ...
    } else { /* require the "g.sN." prefix */ }

So `(g, var)` does not point at a single site; it points at the `var` loop in
*all* of `g`'s stages. What keeps that from injecting `f` into stages that don't
use it is a separate **use gate**: when the injector finds a matching loop it
calls `build_pipeline_group(body)` (`InjectFunctionRealization::visit(const For
*)`, `src/ScheduleFunctions.cpp` ~1299), and `build_pipeline_group` skips any
func not referenced in that body:

    // src/ScheduleFunctions.cpp ~1682 (build_pipeline_group)
    bool should_skip = function_is_already_realized_in_stmt(funcs[i], consumer) ||
                       !(function_is_used_in_stmt(funcs[i], consumer) || is_output_list[i]);

Net rule: `f` is realized just inside the `var` loop of **each stage of `g`
that reads `f`**, and each such realization computes its own required region
(bounds inference runs per injection), so `f`'s surviving loop count can differ
between stages. This is the source basis for loopdoc §6's "what `(g, var)`
points to" subsection, and for the per-stage `micro_halide_collapses`. Backs
`producer_at_rvar` (only the update stage reads `p`),
`cross_stage_compute_at_shared` (both stages read `p` → injected into both).

### "inline" is a level; "realized" is orthogonal (the terminology wart)

Halide names the default compute level "inline" for *all* Funcs, but only a
pure Func is actually substitutable. The relevant predicates:

    // src/Function.h ~185
    bool is_pure() const {                 // "only a pure definition"
        return has_pure_definition() && !has_update_definition()
                                      && !has_extern_definition();
    }
    // src/Function.cpp ~1074
    bool can_be_inlined() const {          // legal to textually substitute?
        return is_pure() && definition().specializations().empty();
    }

and Halide's own user doc for the *default* schedule (`src/Func.h` ~2568,
`Func::compute_inline`): "Aggressively inline all uses of this function. This is
the default schedule … **For a Func with an update definition, that means it
gets computed as close to the innermost loop as possible.**" So Halide
explicitly uses "inline" for non-pure Funcs it must realize — i.e. in Halide
"inline" (a level) and "realized" (a `produce` block exists) are *not*
opposites. This backs loopdoc §4's terminology call-out.

### The default for a Func with updates: `inline_to_provide`

A Func defaults to `LoopLevel::inlined()` (§2 above). A Func with update
definitions (`!is_pure()`) still at that default level cannot be substituted, so
it is realized around the innermost consumer statement that uses it:

    // src/ScheduleFunctions.cpp ~1358, InjectFunctionRealization::inline_to_provide
    if (provide_name != funcs[0].name() &&
        !funcs[0].is_pure() &&
        funcs[0].schedule().compute_level().is_inlined() &&
        function_is_used_in_stmt(funcs[0], provide_op)) {
        Stmt stmt = build_realize(build_pipeline_group(provide_op), funcs[0], ...);
        ...
    }

`build_realize` emits a `produce`/`consume` around the consumer's leaf
(`Provide`) node — the deepest legal site. Crucially this fires at **each**
`Provide` that uses the Func (it is keyed on the provide node, per use), so when
a non-pure Func is read at different depths in different stages it is placed at
each use's own innermost loop *independently* — a placement no single
`compute_at(g, v)` LoopLevel can express (one `v` cannot be both `g.s0.x` and
`g.s1.r`). It coincides with `compute_at` at the innermost loop only when every
use shares a depth. Backs loopdoc §11 (and `update_default_inline.cpp`,
`weird_histogram_sampling.cpp`, both the single-depth case).
