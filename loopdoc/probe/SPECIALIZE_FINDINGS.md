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

## Legality

The Func that **calls** `compute_with` must have no specializations
(`func_2.definition().specializations().empty()`, ScheduleFunctions ~2455;
`probe_cw_specialize`: `f.specialize(c); f.compute_with(g,y)` → CompileError,
exit 134). The check is on the caller (`func_2`); the target (`func_1`) is not
restricted by this assert.
