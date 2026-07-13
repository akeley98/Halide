# `specialize`: conditional schedule variants

Detail companion to the main [loopdoc.md](../loopdoc.md); section references "§N" point to that document.

Per-definition conditional schedule variants; `print_loop_nest` shows each branch's nest concatenated under one `produce`.

---

`f.specialize(cond)` gives a definition a **conditional variant**: at run time, if
`cond` holds, a specialized schedule is used; otherwise a fallback runs. In the
printed nest this shows up as **several loop nests where you'd expect one** —
Halide emits the branch nests back to back.

### The state it records

`specialize` is per **definition** — the pure definition, or a specific update
stage (`f.update(n).specialize(...)`). Each definition carries an **ordered list
of specializations**; each specialization pairs a condition with **its own copy
of the schedule** (a forked Definition). This per-stage affinity is only visible
on an impure Func (one with update stages): specializing one stage expands only
*that* stage's nest, leaving the others as single nests inside the same
`produce f` ([examples/specialize_update_stage.cpp](../examples/specialize_update_stage.cpp)
specializes only the update stage; [examples/specialize_both_stages.cpp](../examples/specialize_both_stages.cpp)
specializes the pure and update stages independently, giving four nests — two per
stage — under one `produce f`). `f.specialize(cond)`:

* appends a specialization whose schedule is a **copy of the schedule so far** —
  every directive issued on `f` *before* this `specialize()` call
  ([examples/specialize_inherit.cpp](../examples/specialize_inherit.cpp): a `tile`
  before `specialize` is inherited by the branch, which then adds a `split`); and
* returns a **handle** to that copy, so further scheduling on the handle affects
  the branch only.

The forked copy is the **whole definition** — its LHS/RHS index and value
expressions as well as its schedule (§1). So a directive that edits the LHS/RHS,
applied through a branch handle, edits **only that branch**:
`f.update(n).specialize(cond).rfactor(...)` factors that branch's reduction alone
(functional-equivalence-preserving, §1), leaving the other branches and the
fallback with their original definitions (§12 "`rfactor` rewrites a stage's
LHS/RHS").

Directives issued on `f` **after** a `specialize()` call modify the parent
(fallback) schedule, not the already-forked specialization
([examples/specialize_fallback_scope.cpp](../examples/specialize_fallback_scope.cpp):
a `split` added after `specialize` lands on the fallback only). A specialization
may itself be specialized, nesting the variants
([examples/specialize_nested.cpp](../examples/specialize_nested.cpp)).
`f.specialize_fail(msg)` appends a terminal specialization that aborts at run
time instead of providing a fallback; nothing may be specialized after it.

### How it becomes loops

A definition's specialization list lowers to a chain of `if`/`else`: the
specializations become the `if`/`else if` arms, in **declaration order**, with
the unspecialized default as the final `else` — `if cond_0 { branch_0 } else if
cond_1 { branch_1 } … else { fallback }`. "Declaration order" is simply the order
the `specialize()` calls were made in your C++ program (program order); the
conditions are tested first-declared-first, so the first matching arm wins.  Each
branch is a full loop nest built from **that branch's** copied schedule (§16
applied to the fork).

Because a specialization's forked copy is itself a full definition with its own
(initially empty) specialization list (§1), specializations form a **tree**, and
*which handle* you call `specialize` on decides the shape:

* Calling `specialize` again on the **same** `Func`/`Stage` handle appends another
  arm to *that* definition's list — the arms are **siblings**, giving one **flat**
  `if / else if / … / else` chain.
* Calling `specialize` on the **handle returned by** an earlier `specialize`
  descends into that branch and appends to *its* list — a **child**, giving a
  **nested** `if` inside that branch's arm
  ([examples/specialize_nested.cpp](../examples/specialize_nested.cpp) nests one
  branch inside another).

Mixing the two builds an arbitrary tree.
[examples/specialize_tree.cpp](../examples/specialize_tree.cpp) does both: with
`fa = f.specialize(cond_a)`, then `f.specialize(cond_b)` (a **sibling** of
`cond_a`, added to `f`), then `fa.specialize(cond_c)` (a **child** of `cond_a`,
added to `fa`), the result is

```
if cond_a:
  if cond_c: … else: …        // cond_a && cond_c ; cond_a && !cond_c
else:
  if cond_b: … else: …        // !cond_a && cond_b ; !cond_a && !cond_b
```

so `cond_c` is only tested when `cond_a` holds, and `cond_b` only when it does
not — four leaf cases. (The `specialize` handle is a `Stage`, so store it as
`Stage fa = f.specialize(cond_a);` — see §13 for why it is not a `Func`.)

`print_loop_nest()` does not print conditions or any `if`/`else` marker: it walks
into **every** branch and prints each branch's loop nest, so the branches appear
**concatenated as sibling subtrees under the same `produce`** node, in that same
order — specialization branches first (declaration order), fallback last
([examples/specialize_basic.cpp](../examples/specialize_basic.cpp): a tiled branch,
4 loops, then the plain fallback, 2 loops, both inside one `produce f`). A
specialized **producer** is no different — its branches sit inside its own
`produce` block ([examples/specialize_producer_self.cpp](../examples/specialize_producer_self.cpp)).

Two consequences of Halide simplifying the nest before printing:

* **`specialize_fail` prints no fallback.** Its else-branch is an assertion, which
  carries no loops, so only the specialization branches appear
  ([examples/specialize_fail.cpp](../examples/specialize_fail.cpp)).
* **Identical branches merge.** If a branch's nest is *identical* to the fallback
  it wraps (same loops, same order, same nested producers — i.e. the
  specialization changed nothing structural), Halide folds the if/else back into
  one copy, so that specialization leaves no trace. A branch that differs only in
  a way this document's structural comparison ignores (the order of two plain
  serial loops, a constant tile size) is still a *distinct* nest and is printed
  separately. In practice every useful specialization changes the structure, so
  each one prints its own subtree; the examples here are all structurally
  distinct per branch.

### Producers under a specialized consumer

A producer computed (or stored/hoisted) at a loop of a specialized consumer is
injected **separately into each branch**, resolved against **that branch's** own
loop nest — each branch has its own copy of the dimension list (§9), so the
producer follows the branch's structure
([examples/specialize_producer_at.cpp](../examples/specialize_producer_at.cpp): the
specialization splits an outer loop, and the `compute_at` producer is injected at
the inner loop of *each* branch's nest). A producer computed **outside** the
consumer (e.g. at `root`) is emitted once, before the consumer, and is not
duplicated per branch
([examples/specialize_producer_root.cpp](../examples/specialize_producer_root.cpp)).
The specialized Func need not be the output or its direct producer: it can sit
deep in the pipeline, with a producer below it and a consumer above
([examples/specialize_midchain.cpp](../examples/specialize_midchain.cpp): the middle
Func of an `a → b → c → out` chain is specialized).

`specialize` forks **only the specialized Func's own schedule**. It does **not**
reach into its callees: a producer is a separate Func with **one** definition and
**one** schedule, shared by every branch. So the only per-branch variation a
producer can show *through scheduling* is its **placement** — which happens when
the `compute_at` level names a loop that the consumer's branch has moved (by a
per-branch `reorder`/`split`/`rename`). It is **impossible, through scheduling**,
to compute a producer *differently internally* (its own splits, its own compute
level) depending on which branch of a consumer uses it — `in`/`clone_in` do not
provide a way either: they redirect a consumer's reads to a **single** wrapper/
clone Func with one schedule, read in all of the consumer's branches (§13).
*Unless* you step outside scheduling entirely and change the algorithm, in the
narrow and fragile way described next.

### Known limitation: no per-branch producer scheduling

A recurring wish is to compute a producer one way when a consumer took its `cond`
branch and another way otherwise. There is **no scheduling directive for this**,
and it is worth stating plainly rather than implying a clean idiom exists.

The only thing that achieves it is an **algorithm** change: give the consumer two
distinct producers and pick between them with `select` —
`f(x,y) = select(cond, g(x,y), gc(x,y))` together with `f.specialize(cond)`. The
specializer simplifies the branch's *value expression* using the known condition,
collapsing the `select` to `g(x,y)` in the `cond` branch (and `gc(x,y)` in the
fallback); as a downstream consequence each branch then references — and so
schedules — only its own producer. This is **not a scheduling technique** and
should be treated as a last resort:

* It edits the algorithm (what `f` computes), so it forfeits Halide's core
  guarantee that scheduling cannot change results. Nothing checks that `g` and
  `gc` are equivalent — if you intend them to be, keeping them in sync is on you,
  unverified.
* It works only as a **side effect** of a value-simplification pass
  (`simplify_specializations`), and only when the specialization condition is a
  bare boolean parameter or `var == const`; for other conditions the `select` is
  not guaranteed to be pruned, in which case *both* producers are scheduled and
  the intended effect **silently** does not happen. See
  [src_doc: specialize](../src_doc/specialize.md) for the mechanism and
  `probe/SPECIALIZE_FINDINGS.md` / `probe/probe_specialize_case2.cpp` for worked
  cases; whether Halide intends this to work at Func granularity is an open
  question, not settled behavior.

This document does not treat `select`-pruning as part of the scheduling model:
**simplifying `select` (and thus this per-branch-producer behavior) depends on
analyzing `Expr`s**, which the loop-nest model here does not do, so it is out of
scope.

### Legality

The Func that **calls** `compute_with` must have no specializations: a fused group
is emitted as one shared, unconditional loop nest (§14), which has no room for a
member's per-branch variants. `f.specialize(cond); f.compute_with(g, v)` is
rejected ([examples/neg_compute_with_specialize.cpp](../examples/neg_compute_with_specialize.cpp)).
The restriction is on the caller (the member being fused in), not on the target
`g` (see [src_doc: compute_with/legality](../src_doc/compute_with/legality.md)).

### Out of scope

* **Condition de-duplication.** Re-calling `specialize` with an *equal* condition
  `Expr` returns the **handle to the existing** specialization rather than
  appending a new one. This document does not model that: the examples always use
  **distinct** conditions (e.g. separate `Param<bool>`s), so each `specialize`
  call is a new branch and no `Expr`-equality bookkeeping is needed.
* **Identical-branch merge.** The "How it becomes loops" note above — that Halide
  folds an if/else whose branches are structurally identical into one printed copy
  — is a `simplify()` effect a purely structural model need **not** reproduce: one
  loop nest per branch (specializations then fallback) without merging is
  equivalent for our purposes. Every example here has structurally distinct
  branches, so no merge arises; matching Halide's merge would require
  true-IR-identity comparison, which is out of scope.
* **Per-branch loop elision.** Placing a producer at a *different loop* per branch
  (e.g. `compute_at(f, y)` in one branch, `compute_at(f, x)` in another) gives it
  a different required region — and so a different set of elided (point) loops —
  per branch. Which loops collapse is a bounds question (§7), which this document
  does not derive; per-branch elision asymmetry is not modeled here.

See [src_doc: specialize](../src_doc/specialize.md) for the compiler-level account
(the `Specialization` list, the `IfThenElse` lowering in `build_provide_loop_nest`,
and why the printer shows branches with no condition).

