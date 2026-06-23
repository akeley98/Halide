# Source evidence: loop-nest construction (bootstrap subset)

This file backs the claims in [../loopdoc.md](../loopdoc.md) §§2–12 with
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
`produce` (loopdoc §5, §6, §13 step 1). In the real `realize`/`compile` path the
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
at ~1977 / 2298). This backs loopdoc §5: pure unscheduled Funcs vanish from the nest.

## 3. Realization order

The topological producers-before-consumers order is computed by

    // src/RealizationOrder.cpp
    pair<vector<string>, vector<vector<string>>>
    realization_order(const vector<Function> &outputs,
                      map<string, Function> &env);

`print_loop_nest` calls it (`auto [order, fused_groups] =
realization_order(outputs, env);`) and `schedule_functions` injects each
function's realization in that order. This backs loopdoc §6 ("realization
order") and §13 step 2. Inlined Funcs remain in `env` and so still contribute
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
its calls in IR order). This is the source basis for loopdoc §6's tie-break
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

Two facts in loopdoc §6 come directly from here:

* The **output gets no `consume`**: `is_output_list[i]` suppresses it (and for
  the very first realization the consumer is a no-op and is dropped).
* Each realization is `Block(produce, consume)` with the consume wrapping the
  *rest* of the program — which is how the chain in loopdoc §6 nests.

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
This backs loopdoc §7's nesting picture and §13 steps 3–4.

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

This is the source basis for loopdoc §7 "When a compute_at is illegal":

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
is the source-level basis for the caveat in loopdoc §7: the loop *count* of a
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
§9 (see `examples/compute_at_elided_level.cpp`). Because predicting min == max
requires the full bounds model, loopdoc declares elision via the `micro_halide_collapses`
annotation rather than deriving it; that annotation has no counterpart in the
real compiler (it is a no-op shim, `halide_compat/halide_compat.h`).

## 8. store_at / store_root: the `store` node

Backs loopdoc §8.

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
this is why loopdoc §8's `store_root` node prints outside the output's
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
compute" (loopdoc §8), and for `store_root().compute_root()` printing no store
node (both at root, so equal).

### Per host stage (store node follows the produce)

`store_level().match(for_loop->name)` is tested on the loop names actually
emitted, which are per stage (`host.s0.v`, `host.s1.v`, … — the stage-wildcard
`match` accepts any of them, §11). The `Realize` is injected into the same
mutated stage body that received the func's `produce`/`consume`, and that
injection is itself per-stage and use-gated (§7: a producer is realized only in
the host stages whose body uses it). So when the host has several stages, the
`store` node lands at `v` only in the stages that actually compute the func, not
in a host stage that merely owns a matching `v` loop. Backs loopdoc §8's
"per host stage" paragraph and `store_at_update_stage` (producer read only in
the update stage) / `rfactor_intm_store_at` (intermediate stored at the merge
stage, absent from the pure stage). (micro_halide initially emitted the store
node in the first stage owning the store-level loop, regardless of where the
func is computed — see progress.txt.)

### Legality

`validate_schedule` (~2285) looks up the requested store and compute levels in
the list of legal `Site`s (the enclosing-loop stack intersection from
`ComputeLegalSchedules`, see §7 above) and requires the store site to be found
*before* (i.e. outside) the compute site:

    // src/ScheduleFunctions.cpp (~2333)
    if (sites[i].loop_level.match(store_at) && hoist_storage_idx >= 0) store_idx = i;
    if (sites[i].loop_level.match(compute_at) && store_idx >= 0 ...)   compute_idx = i;
    ...
    if (!all_ok()) user_error << "... is computed at the following invalid location ...";

Because `compute_idx` is only set once `store_idx >= 0`, a store level inside
the compute level can never satisfy both — hence loopdoc §8's "store must
enclose compute" and `neg_store_inside_compute.cpp`. Separately, a store level
on an inlined Func is rejected up front:

    // src/ScheduleFunctions.cpp (~2300)
    user_error << "Func \"" << f.name() << "\" is scheduled store_at(), but is "
               << "inlined. Funcs that use store_at must also call compute_at.\n";

which backs `neg_store_at_inlined.cpp`.

## 9. hoist_storage / hoist_storage_root: invisible to print_loop_nest

Backs loopdoc §8's hoist-storage subsection.

### No print effect

`hoist_storage` introduces a third level (the hoist-storage level) that only
controls where the physical allocation is emitted; it does not trigger sliding
window. `print_loop_nest`'s visitor (`src/PrintLoopNest.cpp`) has handlers only
for `For`, `Realize` (the `store` line, gated on store != compute), 
`ProducerConsumer`, `Provide`, and `LetStmt`. There is no handler keyed on the
hoist-storage level, so changing it does not change the printed nest. (In the
full lowering pipeline the hoist level affects where the allocation is
physically placed, but that is past what `print_loop_nest` shows.) This backs
loopdoc §8: a legal `hoist_storage` schedule prints identically to the same
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

Backs loopdoc §9. These directives never touch the producer/consumer graph or
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
`src/ScheduleFunctions.cpp` (see §6 above) emit one `For` per `Dim`, outermost
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

The `For` printer (§6) emits `op->for_type` and `simplify_var_name(op->name)`,
and prints `" in [min, max]"` only for constant bounds. The test harness's
`canonicalize.py` then drops the var name entirely and drops constant bounds.
A serial-loop `reorder` changes only names and the order of otherwise-identical
`for` lines, both erased — hence loopdoc §9's "invisible except through a
topological consequence". The consequence is real because `compute_at`
injection (§7 above) matches the *level name* in the post-reorder `dims`: moving
a dim inward/outward moves the loop a producer is filed under, changing how many
host loops land inside its `consume`. Backs `reorder_topological.cpp` vs
`reorder_baseline.cpp`. (A loop-type change — `parallel`/`vectorize`/`unroll`,
which set `Dim::for_type` — *is* kept by both the printer and canonicalizer, so
reordering typed loops would be visible; that is a later milestone.)

### tile (`Stage::tile`, `src/Func.cpp` ~1754)

The two-var `tile` is implemented as two `split`s followed by a `reorder` of the
four resulting vars to `{xi, yi, xo, yo}` (innermost first), exactly as loopdoc
§7 states. Net: `dims` grows by two. Backs `tile_basic.cpp`.

### Sites are matched post-transform

Because `compute_at`/`store_at` resolve their level by matching the name against
the host's `dims` at scheduling time, the transformed vars are the legal sites,
and consumed vars are gone. `g.compute_at(out, x)` after `out.fuse(x, y, xy)`
fails the `ComputeLegalSchedules` lookup (§7 above) since no loop named `x`
remains — only `xy` (and `outermost`/`root`). Backs
`neg_compute_at_fused_away.cpp` and `split_compute_at.cpp`.

## 11. Update (reduction) definitions: stages

Backs loopdoc §3 (stage structure); the cross-stage compute_at parts are §7.

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
(`func_update_compute_at`). `ComputeLegalSchedules` (§7 above) walks the entire
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
whose body uses `f`**, and each such realization computes its own required
region (bounds inference runs per injection), so `f`'s surviving loop count can
differ between stages. This is the source basis for loopdoc §7's "what
`(g, var)` points to" subsection, and for the per-stage `micro_halide_collapses`.
Backs `producer_at_rvar` (only the update stage reads `p`),
`cross_stage_compute_at_shared` (both stages read `p` → injected into both).

Note the `body`/`consumer` passed to `build_pipeline_group` is the loop body
*after* the recursive `mutate(body)` has already injected inner producers
(`visit(const For *)` mutates the body before testing the compute level). So
`function_is_used_in_stmt` sees **transitive** uses: a producer `g` already
placed in that body brings its own calls to `f` with it. This is why
`f.compute_at(h, v)` works even when `h` never reads `f` directly — an
intermediate `g` (computed at `h.v`) is in the body, so the body "uses" `f` and
`f` is injected at `h.v` before `g`. Backs loopdoc §7 "Computing at an indirect
consumer's loop" / `transitive_compute_at_outer.cpp`.

Crucially this is all **per stage**, and self-grounding: the test is run on each
stage's own already-mutated body, and `g` itself was injected into that body only
if `function_is_used_in_stmt(g, that_stage_body)` held. So the transitive chain
bottoms out in *direct* reads by the stage: `f` lands in stage `s` iff `s`'s body
contains a call to `g` (recursively) at the `v` loop **and** `g` calls `f`. A
stage whose body never calls `g` (e.g. an `rfactor` intermediate's pure `= 0`
stage, which reads nothing) gets neither `g` nor `f`, even though the
stage-wildcard `LoopLevel(h, v)` matches a `v` loop in it. Backs loopdoc §7's
per-stage indirect-pull paragraph and `rfactor_indirect_at_intm` /
`rfactor_indirect_nested` / `neg_rfactor_indirect_h_at_intm`. (micro_halide's
`body_uses` recursion correctly grounds the *direct* transitive case from the
earlier milestone, but initially counted an intermediate `g` as present in a
stage merely because the stage had a loop matching `g`'s compute level — without
checking `g` is itself used there — so it wrongly pulled the indirect `f` into a
non-using stage; see progress.txt.)

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

## 12. rfactor: a new Func plus a rewritten update

`Stage::rfactor` (`src/Func.cpp` ~857, `rfactor(const vector<pair<RVar,Var>>&)`)
constructs a fresh intermediate `Function` and mutates the update `Definition`
it was called on, in one pass. The structural facts loopdoc §12 relies on are
all visible here; the associativity machinery (`prove_associativity`,
`rfactor_validate_args`) is the semantic part `micro_halide` does not model.

* **Update-only.** `user_assert(!definition.is_init())` (line 858): rfactor on
  the pure stage is rejected.

* **Naming.** `Func intm(function.name() + "_intm")` (line 953). The harness
  normalises names, so only the existence of one new distinct Func matters.

* **Intermediate pure stage** (lines 955–960): `intm(args) = Tuple(identities)`
  where `args = dim_vars ++ preserved_vars` — the original Func's pure args
  followed by the new pure Vars. A pure def's `dims()` is innermost-first in arg
  order, so the new Vars land outermost: `[x, u]` for `rfactor(r.y, u)`.

* **Intermediate update stage** (lines 962–1009): args/values are the original
  update's, with the preserved RVars substituted by the new Vars
  (`intermediate_map`) and self-references redirected to `intm`. Its schedule is
  a *copy of the original update's schedule* (`= definition.schedule().get_copy()`,
  line 1005): `intm_dims = definition.schedule().dims()` (line 976) — so it
  inherits any prior `split`/`reorder` — then each preserved RVar dim is replaced
  in place by its pure-Var dim (lines 979–990), and the factored pure Vars are
  inserted `intm_dims.end() - 1` i.e. just inside the `__outermost` sentinel
  (lines 992–1003). Non-preserved RVars remain as the intermediate's reduction
  (`intermediate_rdims`, lines 904–908).

* **The original update is rewritten into the merge** (lines 1011–1071):
  `definition.values()` become the associative combine reading `intm(...)`
  (the `preserved_map` lets bind the previous value and the partial); the dim
  list keeps every non-RVar dim and the preserved-RVar dims and **drops the
  non-preserved RVars** (lines 1040–1043); any pure Var the original update did
  not mention is re-added before `__outermost` (lines 1057–1062, the histogram
  case). So the merge reduces over only the preserved RVars and now reads `intm`,
  making `intm` a producer of the original Func (hence its realization-order slot
  before it, §3 above).

The returned `intm` is an ordinary Func at the default `inlined()` level, so
absent a schedule it follows §11's `inline_to_provide` (realized at its use in
the merge). Backs loopdoc §12 and `rfactor_basic`, `rfactor_default_inline`,
`rfactor_compute_at`, `rfactor_multivar`.

Note the schedule copy at line 976 (`intm_dims = definition.schedule().dims()`)
inherits any `split` already applied to the factored stage — including a split
of the factored `RVar` itself (lesson 18's `split(r.x, rxo, rxi).rfactor(rxo)`).
Modelling that needs `RVar` splitting (the sub-vars `rxo`/`rxi` are reduction
loops that the merge-drop step at lines 1037–1044 must still recognise as
RVars), which `loopdoc.md` defers; `rfactor_multivar` factors whole `RVar`s of a
3-D `RDom` to avoid it.

## 13. in() / clone_in(): wrappers and clones

Backs loopdoc's wrappers section (`in()` / `clone_in()`, not yet written at the
time of this note). This section is deliberately detailed because the
machinery is subtle and easy to break when maintaining Halide. Two questions
drive it: (a) how the compiler tells apart Funcs that *look* like they share a
name, and (b) what state `f.in(fs)` / `f.clone_in(fs)` actually mutates — in
particular, what (if anything) happens to the consumer Funcs in `fs`.

### API entry points

`Func::in(const Func&)`, `in(const vector<Func>&)`, `in()` (global),
`clone_in(const Func&)`, `clone_in(const vector<Func>&)` (`src/Func.cpp` ~2299–2330)
all funnel into one helper:

    Func get_wrapper(Function wrapped_fn, string wrapper_name,
                     const vector<Func> &fs, bool clone);   // ~2242

with `wrapper_name` built from the wrapped Func's name:
`<wrapped>_in_<consumer>`, `<wrapped>_in`, `<wrapped>_clone_in_<consumer>`, or
`<wrapped>_clone`. `get_wrapper` then appends a uniqueness suffix
`"$" + to_string(wrappers.size())` (~2248), so repeated wrappers of the same
Func get distinct names (`f_in_g$0`, `f_in_g$1`, …).

### (a) How "same-named" Funcs are actually distinguished

They are not same-named. A `Function` is a handle to a `FunctionContents`
addressed by a `FunctionPtr` (a pointer into a `FunctionGroup` plus an index)
**and** carries a unique `name` string. A wrapper/clone is a *new, distinctly
named* Function:

* `new_function_in_same_group(name)` (`src/Function.cpp` ~1219) appends a fresh
  member to the wrapped Func's `FunctionGroup` and returns a `FunctionPtr` to it.
  The **group** is purely a storage/lifetime device: mutually-referencing
  Functions live in one group so within-group edges can be *weak* `FunctionPtr`s,
  which is how Halide avoids reference cycles among a Func and its wrappers. Group
  membership is **not** identity; the `name` (and the `FunctionPtr` it resolves
  to) is.
* The pretty `f.in(g)` string is only a *profiler display name*
  (`set_profiler_display_name`, used in `get_wrapper` ~2267) for tracing output.
  It is cosmetic and never used as identity.
* `print_loop_nest` prints `simplify_func_name(name)` (`src/PrintLoopNest.cpp`
  ~55): it keeps the Func name, drops the `.sN.` stage tag, and truncates at the
  first `$`. So `f_in_g$0` prints as `f_in_g`. (The loopdoc harness then maps
  every distinct name to a positional id, so what is actually verified is that
  the wrapper is a *separate Func node* in the nest, not its spelling.)

### (b) What state changes — and, crucially, what does NOT

`in()/clone_in()` do **not** modify the consumer Funcs in `fs`. The only Func
mutated at call time is the **wrapped** Func, plus creation of the new wrapper:

1. **The wrapper Func is built** (`get_wrapper` ~2257):
   * `create_in_wrapper` (~2169): a fresh *pure* Func whose single definition is
     `wrapper(args) = wrapped(args)` — a pointwise identity that reads the wrapped
     Func. That is the whole body; it is what makes an `in` wrapper a thin
     "caching"/redirection layer.
   * `create_clone_wrapper` (~2176): `deep_copy`s the wrapped Func's **own**
     `FunctionContents` (its init/update `Definition`s, schedule, specializations,
     reduction domains) into the new member, then `substitute_calls` remaps the
     clone's **self-references** to point at the clone itself (weakened). A clone
     is an independent duplicate of *that one Func's* definition + schedule +
     storage; an `in` wrapper is a one-line reader of the original.
     **Its callees are NOT duplicated.** The clone's copied definition expressions
     still hold the *same* `FunctionPtr`s to whatever the original called, so the
     clone reads the *shared* producers. Two independent checks confirm this:
       - **Produce count.** With a 2-level callee chain `q <- p <- f`,
         `f.clone_in(g)` (everything `compute_root`) prints exactly one
         `produce q` and one `produce p`; the clone `f_clone_in_g` reads the same
         `p`. A recursive callee copy would print each twice.
       - **Legality discriminator.** With `f(x)=p(x)`, `f.clone_in(g)`, then
         `p.compute_at(f, x)`, Halide rejects the schedule and its own diagnostic
         lists the uses of `p`: *"`f_clone_in_g$0` uses p"* **and** *"`f` uses
         p"*, with the only legal location `p.compute_root()`. If the clone had a
         private copy of `p`, then `p` would be used by `f` alone and
         `p.compute_at(f, x)` would be legal. (It is illegal, so `p` is shared.)
     Do **not** mis-cite `Func::clone_in`'s "Only this Func is cloned … the
     intermediate Funcs along the path are not" for this: that sentence is about
     the transitive *caller* chain (the Funcs *between* `fs` and the wrapped Func,
     e.g. `sum()`'s anonymous reduction Func), not the wrapped Func's callees. It
     happens to be *consistent* with callee-sharing but is not evidence for it.
     The evidence is the two checks above (and the source trace in the verdict at
     the end of this section).

2. **The mapping is recorded on the WRAPPED Func** (`add_wrapper`,
   `src/Function.cpp` ~1229): it inserts into `wrapped.func_schedule.wrappers()`,
   a `map<string, FunctionPtr>` keyed by **consumer name** (`src/Schedule.cpp`
   ~443); the empty key `""` denotes a *global* wrapper. `add_wrapper` also
   (i) **freezes** the wrapper (`wrapper.freeze()`) so its definition/schedule can
   no longer be edited — this is why you may schedule the returned handle but not
   redefine it — and (ii) **weakens** the `FunctionPtr`s in both directions (the
   map entry, and the wrapper's back-references via `WeakenFunctionPtrs`) to keep
   the group acyclic for refcounting.

   The consumer Funcs in `fs` are **untouched** here. Nothing in `g`'s
   `FunctionContents` changes when you call `f.in(g)`.

3. **`fs` is first normalized to direct callers** (`resolve_transitive_callers`
   ~2219, via `collect_direct_callers_of` ~2193): each `f` in `fs` is replaced by
   the set of Funcs that *directly* call the wrapped Func on a path down from `f`.
   So `f.in(h)` where `h` reaches `f` only through `g` actually registers the
   wrapper under `g` (the direct caller). A Func with no static path to the
   wrapped Func is left as-is (the wrapper is registered under its own name and
   simply never triggers).

### When `fs` is actually rewired: `wrap_func_calls` at lower time

The call substitution is **deferred** to a lowering pass, `wrap_func_calls`
(`src/WrapCalls.cpp`), which `print_loop_nest` runs explicitly
(`src/PrintLoopNest.cpp` ~184; `Lower.cpp` ~164 for the real pipeline). Operating
on the *environment* (a `map<name, Function>` for this realization — a working
copy, not the user's handles):

1. For every Func in `env` and each entry of its `wrappers()` map it builds
   `func_wrappers_map : consumer FunctionPtr -> { wrapped FunctionPtr -> wrapper
   FunctionPtr }`:
   * **custom** wrapper (key = consumer name): substitution registered for that
     consumer only;
   * **global** wrapper (key `""`): registered for *every* Func except the wrapped
     Func itself and the wrapped Func's own wrappers (so the wrapper still reads
     the original), and except consumers that already have a custom wrapper for
     this wrapped Func (custom takes precedence).
2. For each consumer it calls `Function::substitute_calls(substitutions)`
   (`src/Function.cpp` ~1265), which walks the consumer's IR and rewrites every
   `Call` whose `func` is the wrapped Func to instead name the wrapper
   `FunctionPtr`. **This is the only place a consumer's body changes**, and it
   happens on the lowering-time environment copy.
3. `validate_custom_wrapper` (~53) then asserts each custom wrapper's consumer
   really did call the wrapped Func; otherwise `user_error` "Cannot wrap … does
   not call …". This is the `f.in(g)` where `g` never reads `f` error, checked
   *after* substitution so chained wrappers (`f.in(g).in(g)`) validate correctly.

Wrappers are pulled into the environment in the first place by
`populate_environment` / `FindCalls` with `include_wrappers` set: a Func's
`wrappers()` targets are inserted into `env` (`src/FindCalls.cpp` ~76). So a
wrapper is an ordinary Func for realization-order and loop-nest purposes — a
producer of each consumer it was inserted for, and itself a consumer of the
wrapped Func.

Net effect for `f.in(g)` with `g(x) = f(x) + f(x+1)`, all `compute_root`
(verified): realization order is `f`, then `f_in_g`, then `g`; `g`'s two reads of
`f` become reads of `f_in_g`; `f_in_g(x) = f(x)` reads `f`. The wrapper is a
normal node in the nest.

### Implication for micro_halide (representation note)

The Halide design answers the "do I have to hunt down and rewrite every consumer
that references `f`?" worry: **no.** The wrapper relationship is stored once, on
the *wrapped* Func, keyed by consumer name, and the call rewrite is applied as a
*derived* step (`wrap_func_calls`) when the nest is built — not as an eager
mutation of consumer state at `in()` time. A micro_halide that mirrors this
(record `{consumer_name -> wrapper}` on the wrapped Func; resolve producer→wrapper
redirection while walking producers during nest construction) needs no
tree-search over consumer `shared_ptr<FuncContents>` at `in()` time. The eager
alternative — rewriting every consumer's producer pointers when `in()` is called
— is *not* what Halide does and is the churn worth avoiding.

### Verdict on the `Function::deep_copy` header comment

The header comment (`src/Function.h`) on `Function::deep_copy` reads:

> Deep copy this Function into 'copy'. It recursively deep copies all called
> functions, schedules, update definitions, extern func arguments,
> specializations, and reduction domains. … This method also takes a map of
> <old Function, deep-copied version> as input and would use the deep-copied
> Function from the map if exists instead of creating a new deep-copy …

**Verdict: the comment is accurate for the method's intended *whole-pipeline*
use, but misleading about the method *in isolation* — the member
`Function::deep_copy` does not by itself recurse into or copy called functions.**
What the body actually does (`src/Function.cpp` ~497):

* It copies *this* Function's own components: scalar fields, `func_schedule`
  (`FuncSchedule::deep_copy`), `init_def` and each update via
  `Definition::get_copy()`, and extern arguments. `get_copy()`
  (`src/Definition.cpp` ~120) copies the `Definition`'s `values`/`args` `Expr`s
  by plain assignment — and copying an `Expr` is a shallow `IntrusivePtr` share,
  so every `Call` node keeps the **same** `FunctionPtr` to the original callee.
  No callee `FunctionContents` is created here.
* `copied_map` is *consulted* (not populated with new copies) in exactly one
  place inside the method: `deep_copy_extern_func_argument_helper` (~470), which
  looks up an extern-arg callee and `internal_assert`s it is **already** in the
  map. Regular `Call` expressions are not remapped by this method at all.

The "recursively … all called functions" behavior is realized by the **free
function** `deep_copy(const vector<Function>&, const map<string,Function>&)`
(`src/Function.cpp` ~1304), the *caller* that the comment tacitly assumes: it
pre-seeds `copied_map` with an empty copy of **every** Function in the
environment, calls the member `deep_copy` on each, and *then* runs
`substitute_calls(copied_map)` to repoint every `Call` to its copy. The
recursion/coverage is that caller's loop plus the separate `substitute_calls`
pass — not logic inside the member. So the comment describes the *cooperating
protocol's* end-to-end effect and pins it on the member method.

This is exactly why `clone_in` shares callees: `create_clone_wrapper` drives the
member `deep_copy` with a `copied_map` seeded **only** with the wrapped Func's
self-reference, and runs `substitute_calls` for **only** `{wrapped -> clone}`.
With no callees in the map and no env-wide substitution, the copied definition's
`Call`s keep pointing at the originals — the clone shares them. The
`Function::deep_copy` comment overstates the member's behavior.

(The user's hypothesis — that "copying a function" has a subtler internal meaning
than a scheduling-visible copy — is essentially right: the member copies one
Func's *structure*, and "all called functions" is achieved only when a caller
supplies the full `copied_map` and a follow-up `substitute_calls`.)

A separate caution, noted above: the `Func::clone_in` user-doc phrase "the
intermediate Funcs along the path are not [cloned]" is about the transitive
*caller* chain, **not** callees, so it is not independent evidence here. The
callee-sharing claim rests on the source trace plus the two empirical checks in
part (b): a single `produce p`/`produce q` over a 2-level callee chain, and
Halide's own legality diagnostic naming both `f` and `f_clone_in_g$0` as users
of the shared `p`.

**Confidence: high (~0.95).** Grounded in: the member body (no callee creation),
`Definition::get_copy` (shallow `Expr`/`FunctionPtr` share), the free
`deep_copy` + `substitute_calls` protocol, `create_clone_wrapper`'s self-only
remapping, and **two** empirical discriminators (produce-count and the
`compute_at` legality error that explicitly lists the clone as a user of the
shared callee). Residual uncertainty: I did not line-by-line audit
`FuncSchedule::deep_copy` or specialization copying for some hidden
Function-creating path, but the empirical results rule out callee duplication
along the `clone_in` path regardless, so any such path would not change the
verdict for the behavior that matters here.
