# Update (reduction) definitions: stages

_Part of the [src_doc set](README.md); sections keep their global numbers (§1–§14), and cross-file references are written as "§N"._

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
`f.update(0)` rewrites only that update's `dims` (§10; `split`/`reorder`
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
(`func_update_compute_at`). `ComputeLegalSchedules` (§7) walks the entire
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

A Func defaults to `LoopLevel::inlined()` (§2). A Func with update
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
