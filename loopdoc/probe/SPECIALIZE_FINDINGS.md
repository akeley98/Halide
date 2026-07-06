# Findings: how does `specialize` map to `print_loop_nest()` output?

Probes `probe_specialize*.cpp`, `probe_cw_specialize.cpp`, `probe_collapse.cpp`,
`probe_inherit.cpp` in this directory, run against the locally-built real Halide
(`../../build`). `print_loop_nest()` writes to **stderr**.

## State

Each `Definition` (the pure def and each update) carries an ordered list of
`Specialization{condition, definition}`. `f.specialize(cond)`:
- appends a specialization whose `.definition` is a **copy of the schedule so
  far** (every directive issued on `f` *before* this `specialize()` call), and
- returns a `Stage` handle to that copy.

Directives issued on `f` **after** a `specialize()` call modify the parent
(fallback) schedule only, not the already-forked specialization
(`probe_inherit` case 2: a `split` added after `specialize` appears only in the
fallback branch). Re-calling `specialize(cond)` with the same condition returns
the existing handle. Specializations may themselves be specialized (nested).
`specialize_fail(msg)` appends a terminal specialization (const-true condition +
failure message); nothing may follow it.

## Lowering (ScheduleFunctions.cpp ~518-544)

Build the fallback nest from the def's own schedule, then for each
specialization `i` from **last to first** wrap:
`stmt = IfThenElse(cond_i, then_i, stmt)`, where `then_i` is the nest built from
specialization `i`'s copied schedule. `specialize_fail` becomes
`AssertStmt(false, ...)` as the innermost else. Net structure:
`if cond_0 {s0} else if cond_1 {s1} ... else {fallback}`, nested-if for nested
specializations.

## Rendering (PrintLoopNest.cpp — NO `visit(IfThenElse)`)

The default `IRVisitor` walks both branches and prints **neither the condition
nor any if/else marker**. So each branch's loop nest is printed **concatenated as
sibling subtrees**, in order: specialization 0, specialization 1, …, fallback
**last**. All branches sit under the **same** `produce`/`consume` node of the
specialized Func (the if/else lives *inside* `produce f`). Confirmed:
- `probe_specialize` case1 (tile branch, 4 loops; then fallback, 2 loops).
- `probe_specialize2` caseB (three branches: tile / split / fallback).

Two simplifier effects (PrintLoopNest runs `simplify(s)` at the end):
- **Identical-branch collapse**: an `if/else` whose branches are identical IR
  merges to one. A specialization that produced a byte-identical nest to its
  fallback **disappears** (`probe_collapse` case 1: bare `specialize(c)` with no
  schedule change → one nest). A branch that differs only in a way the
  *canonicalizer* ignores (serial-loop order, constant bounds) is still distinct
  IR and prints separately (`probe_collapse` case 2: `reorder(y,x)` → two
  branches `for x;for y` then `for y;for x`). **Trap for micro:** the merge is
  keyed on TRUE IR identity, not canonical identity — micro must emit one copy
  per branch and merge two adjacent copies only when they match in full detail.
- **`specialize_fail`**: its else is an assert → prints nothing → the fallback
  subtree is absent; only the specialization branches print (`probe_specialize`
  case3).

## Inheritance (`probe_inherit`)

The specialization branch = (schedule at the moment `specialize` was called) +
its own later directives. `tile` before `specialize`, `split` on the branch →
branch has tile **and** split (5 loops); fallback has tile only (4 loops).

## Producers under a specialized consumer

A producer computed/stored/hoisted at a loop of the specialized consumer is
injected **separately into each branch** (the §7 rule), using **that branch's**
(possibly renamed / re-split) loop nest. The producer's own schedule is global
(one schedule); the only per-branch variation is its **placement**, achieved via
renaming loops in the consumer's specialized schedule so the `compute_at`
loop-name resolves to different depths — the Func.h `g_loop` trick
(`probe_specialize2` caseA: `g` at `f.y` in the specialized branch, at `f.x` in
the fallback). A producer that is itself specialized just carries the if/else
inside its own `produce` node (`probe_specialize3`).

## No transitive specialization

`specialize` forks **only the specialized Func's own schedule**. Callees are
shared Funcs with one definition and one schedule; there is no mechanism to
schedule a callee's *internals* differently across a consumer's branches. Of the
human's three cases, only case 1 (per-branch **placement**) is achievable; cases
2/3 (per-branch internal schedule of a callee / callee-of-callee) are not
expressible via `specialize` — they would require genuinely separate Funcs.

## Per-branch producer scheduling (user "case 2/3"): no schedule-only mechanism

`probe_specialize_case2.cpp` and `probe_specialize_deadbranch.cpp`.

There is **no scheduling directive** that makes a producer computed *differently
internally* depending on which specialized branch of its consumer uses it. A
producer is a separate Func with **one** schedule (plus its own specialization
tree), shared by every branch of every consumer. `specialize()` forks only the
specialized Func's own schedule, never its callees.

- **`clone_in(f)` / `in(f)` do NOT help.** They redirect a *consumer's* reads to
  **one** wrapper/clone Func with **one** schedule. `f` reads that single Func in
  **all** its branches. `probe_specialize_case2.cpp` (1)/(2): with `f`'s branches
  made structurally distinct, the one clone/wrapper appears in *both* branches
  tiled **identically** — there is no handle to schedule it per branch.

- **`select(cond, g, gc)` is the only thing that yields per-branch producers, and
  it is an ALGORITHM construct, not a schedule.** With `f(x,y) =
  select(cond, g, gc)` and `f.specialize(cond)`, `simplify_specializations` prunes
  the select per branch, so branch A injects only `g` and the fallback only `gc`,
  each with its independent schedule (`probe_specialize_case2.cpp` (3): tiled `g`
  in one branch, plain `gc` in the other). But this **changes what `f` computes**
  and carries **no equivalence guarantee** — Halide never checks `g == gc` (the
  probe makes them differ, `gc = in + 1`, and it compiles happily). So it steps
  outside Halide's algorithm/schedule separation and its "rescheduling can't
  change results" safety net. It is a workaround, not a scheduling answer. Case 3
  (vary a grand-producer `h` per `f`-branch) is the same one level deeper:
  duplicate the sub-chain (`g_a/h_a` vs `g_b/h_b`) and `select` between them.

### When is the dead branch eliminated? (early, so it imposes no constraints)

`print_loop_nest` order (PrintLoopNest.cpp): `realization_order` →
`simplify_specializations(env)` → `schedule_functions`. The select is pruned per
branch **before** the scheduler injects/validates producers, so a producer that
is dead in a branch is never injected there and imposes **no** scheduling
constraint there. `probe_specialize_deadbranch.cpp`:

- (b) `g.compute_at(f, xi)` where `xi` exists only in `g`'s live (tiled) branch
  and is **absent from the fallback** (where `g` is dead) → **legal**. The dead
  side's lack of `xi` is irrelevant.
- (c) contrast: `g.compute_at(f, x)` where `g`'s **live** branch tiled `x` away →
  **error**, and the "legal locations" Halide lists are that live branch's tile
  loops (`xo/yo/xi/yi`), not the fallback's `x`.

So the worry that "scheduling on the dead side triggers illegal-schedule errors"
does **not** materialize. A producer's compute_at level need only be valid in the
branch(es) where it is actually used. (Func.h's "the Var must exist in all paths"
means all paths where the producer is *used*; with `select`, that is its single
live branch.)

## Legality

The Func that **calls** `compute_with` must have no specializations
(`func_2.definition().specializations().empty()`, ScheduleFunctions ~2455;
`probe_cw_specialize`: `f.specialize(c); f.compute_with(g,y)` → CompileError,
exit 134). The check is on the caller (`func_2`); the target (`func_1`) is not
restricted by this assert.
