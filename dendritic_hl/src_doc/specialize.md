# §15 — specialize: conditional schedule variants

Backs [../loopdoc.md](../loopdoc.md) §15. Paths are relative to the Halide source
root (`../src` from the loopdoc directory); line numbers are approximate, function
names are the stable anchors.

## The state: a `Specialization` list per Definition

Each `Definition` (the init definition, or an update stage) owns a
`vector<Specialization> specializations` (`src/Definition.cpp:24`, exposed via
`Definition::specializations()`, `src/Definition.h:122`). A `Specialization`
(`src/Schedule.h`) pairs a `condition` `Expr`, a nested `Definition definition`
(its forked schedule), and a `failure_message` string.

`Stage::specialize(const Expr &condition)` (`src/Func.cpp:1394`; `Func::specialize`
at `2472` forwards to it for the init definition):

1. asserts the condition is `bool` and depends on **no Var/RVar**
   (`CheckForFreeVars`, ~1399) — conditions are runtime scalars only;
2. if an existing specialization has an `equal` condition, **returns a handle to
   it** rather than adding a new one (~1410–1415) — the de-duplication loopdoc §15
   lists as out of scope;
3. otherwise asserts we are not past a `specialize_fail` (~1418) and calls
   `definition.add_specialization(condition)`.

`Definition::add_specialization` (`src/Definition.cpp:203`) builds the fork:
it copies the parent's values/args/predicate and, crucially,
`s.definition.contents->stage_schedule = contents->stage_schedule.get_copy();`
— **the sub-schedule inherits everything about its parent except its own
specializations.** This is loopdoc §15's "copy of the schedule so far": directives
issued *before* `specialize()` are in the copy; the returned handle
(`Stage(function, s.definition, stage_index)`, ~1422) points at the fork, so
directives issued *after* on that handle mutate the branch, while directives on the
original `Func`/`Stage` mutate the parent (fallback) schedule. Nested
`specialize` just calls the same code on the fork's Definition.

`Stage::specialize_fail(message)` (`src/Func.cpp:1425`) adds a specialization with
`const_true()` condition and a non-empty `failure_message`; the assert at ~1428
enforces at most one, and (via the ~1418 guard on the next `specialize`) that it is
terminal.

## Lowering: nested `IfThenElse`, fallback last

`build_provide_loop_nest` (`src/ScheduleFunctions.cpp` ~518–544) builds the
definition's own nest as the fallback `stmt`, then folds the specializations in
**reverse** (`for (i = specializations.size(); i > 0; i--)`):

```cpp
Stmt stmt = build_loop_nest(body, prefix, ..., def);      // fallback
for (size_t i = specializations.size(); i > 0; i--) {
    const Specialization &s = specializations[i - 1];
    if (s.failure_message.empty()) {
        Stmt then_case = build_provide_loop_nest(env, prefix, func, s.definition, ...);
        stmt = IfThenElse::make(s.condition, then_case, stmt);
    } else {
        // specialize_fail: the else clause is an assert, no loops
        stmt = AssertStmt::make(const_false(), specialize_fail_error);
    }
}
```

Folding last-to-first yields `if cond_0 { s0 } else if cond_1 { s1 } … else
{ fallback }` in **declaration order, fallback last** — each `then_case` is a full
recursive `build_provide_loop_nest` on the fork, so a branch's own splits/tiles and
its own compute_at producers are baked into that branch. `specialize_fail`'s branch
becomes an `AssertStmt(const_false, …)` as the innermost else — it carries **no
loops**, so nothing prints for the fallback (loopdoc §15).

## Printing: no `IfThenElse` visitor, then `simplify`

`PrintLoopNest` (`src/PrintLoopNest.cpp`) has **no `visit(const IfThenElse*)`**
override, so the base `IRVisitor` recurses into `condition`, `then_case`, and
`else_case` and prints **nothing** for the `if` itself. The two branches' loop
nests are therefore printed **concatenated as siblings**, in `then`-before-`else`
order — matching the declaration order above — with no condition text.

Two simplifier effects run before printing (`print_loop_nest`,
`src/PrintLoopNest.cpp` ~192, ~219):

- `simplify_specializations(env)` (~192) propagates each specialization's condition
  into its branch's RHS/LHS (so, e.g., a `select` guarded by the condition
  collapses inside the branch). It does not change loop structure.
- the final `simplify(s)` (~219) folds an `IfThenElse` whose `then`/`else` are
  **identical IR** into one copy — the "identical branches merge" rule of loopdoc
  §15. The identity is on true IR (loop names and order included), so a branch that
  differs only in ways the loopdoc canonicalizer drops (serial-loop order, constant
  bounds) is *not* merged and prints separately.

## Per-branch producers: an off-label side effect, not a supported feature

`specialize` never forks a callee — `add_specialization` copies only *this*
Definition's `stage_schedule`, and callees are shared Funcs in `env` with one
schedule each. So there is no schedule-only way to compute a producer differently
per consumer branch; `in`/`clone_in` likewise produce a single wrapper/clone Func
(one schedule) read in every branch. (A `specialize()` handle is a `Stage`, not a
`Func`, so it cannot even be passed to `in`/`clone_in`, which take a `Func`
consumer — the wrapper is keyed by consumer Func, never by branch.)

The only way to get per-branch producers is an **algorithm** `select(cond, g, gc)`
combined with `f.specialize(cond)`. It is worth being precise about *why* this
works, because it is an emergent side effect of an unrelated pass, not a designed
capability:

- **Specialization itself lowers to `IfThenElse`, not `select`.**
  `build_provide_loop_nest` wraps each branch's separately-scheduled nest with
  `IfThenElse::make(s.condition, then_case, fallback)`
  (`src/ScheduleFunctions.cpp:528`). The compiler never inserts a `select` to
  implement a specialization, so `simplify_specializations` is not there to clean
  up compiler-generated selects.

- **`simplify_specializations` is a value-simplification pass.**
  `propagate_specialization_in_definition` (`src/SimplifySpecializations.cpp`)
  prunes const-false / const-true specializations, and — the relevant part —
  propagates the branch condition as a *known fact* into each branch's own
  `values()` and `args()` (the definition's expressions) and `simplify()`s them.
  Its documented job (Func.h: `f(x) = x + select(cond, 0, 1); f.specialize(cond)`
  → branches computing `x` and `x + 1`) is to make a specialized branch's
  **computed values / bounds** simpler because the condition is known. It runs in
  the real lowering pipeline (`src/Lower.cpp:172`), not just the print path.

- **The per-producer trick rides on that.** `f = select(cond, g(x,y), gc(x,y))`
  is a `values()` expression; the pass simplifies it to `g(x,y)` in the `cond`
  branch. Only *then* — as a downstream consequence in `schedule_functions` — does
  "the branch body references only `g`" cause only `g` to be injected/scheduled
  there. The pass simplifies *expressions*; that this also steers *which producer
  is scheduled* is incidental. Selecting over Func **calls** (rather than the
  scalars in every Func.h example) to exploit it is undocumented, and Halide never
  checks that `g` and `gc` compute the same values.

- **The clean pruning only fires for a bare `Variable` or `var == const`
  condition.** In `propagate_specialization_in_definition` the condition is
  matched as `Variable` or `EQ(Variable, b)`; those get an exact
  `substitute_value_in_var` (`cond → true`, `var → b`). Any other condition falls
  through to `simplify_using_fact`, which keeps a branch expression only if
  `can_prove(!fact || e)` — an implication check. If the algorithm's `select`
  condition is not syntactically the specialization condition (or provably implied
  by it), the `select` **survives**, both `g` and `gc` stay referenced, both get
  scheduled, and the trick fails **silently** (no diagnostic). This fragility is
  the strongest evidence it is not a supported pattern.

### When the dead branch is pruned (relative to schedule legality)

Ordering matters: `simplify_specializations(env)` runs (PrintLoopNest ~192; Lower
~172) **before** `schedule_functions` injects producers and validates their
compute levels. So a producer pruned from a branch's RHS is simply not present in
that branch's body and is never injected there — it imposes no scheduling
constraint on the branch where it is dead. A producer's `compute_at` level is
validated only against the branch(es) whose body actually uses it. (Verified in
`probe/probe_specialize_deadbranch.cpp`: a producer at a tile loop that exists only
in its live branch is legal though the dead fallback lacks that loop; the invalid
case reports the *live* branch's loops as the legal set.)

## Legality: fused members may not be specialized

`compute_with` collection (`src/ScheduleFunctions.cpp` ~2455) asserts
`func_2.definition().specializations().empty()` — the Func that **calls**
`compute_with` (the member being fused into another) must have no specializations
("Func f is scheduled to be computed with g, so it must not have any
specializations."). The check is on the caller (`func_2`), not the target
(`func_1`); see [compute_with/legality.md](compute_with/legality.md). A fused group
is emitted as one shared unconditional nest (§14), with no place for a member's
per-branch variants.
