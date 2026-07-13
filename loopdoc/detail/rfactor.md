# `rfactor`: factoring an associative reduction into a new Func

Detail companion to the main [loopdoc.md](../loopdoc.md); section references "§N" point to that document.

`rfactor` creates a brand-new intermediate Func and rewrites the update stage it was called on into a *merge* that reads the intermediate — splitting a serial reduction into parallelizable partial reductions plus a final merge.

---

`rfactor` is a scheduling directive called on an **update stage**
(`f.update(i).rfactor(...)`), but it is unusual: most directives only reshape
existing loops, whereas `rfactor` changes the *structure* of the algorithm. It
**creates a brand-new intermediate Func** and **rewrites** the update stage it
was called on. It exists to parallelise or vectorise a reduction: a reduction
loop carries a dependence across its iterations (each adds onto the running
result), so it cannot be parallelised directly; `rfactor` splits the work into
independent partial reductions — which *can* be parallelised — plus a final
merge.

`rfactor` takes a list of `{RVar, Var}` pairs, the **preserved** vars (the
shorthand `rfactor(r.x, u)` is one pair). Each named `RVar` of the stage is
mapped to a fresh **pure** `Var`. Conceptually, given

```cpp
f(x)  = 0;
f(x) += in(r.x, r.y);             // update stage: reduces over r.x and r.y
Func intm = f.update(0).rfactor(r.y, u);   // preserve r.y as a new pure Var u
```

the state becomes two Funcs ([examples/rfactor_basic.cpp](../examples/rfactor_basic.cpp)):

```cpp
// the new intermediate Func (auto-named "f_intm"):
intm(x, u)  = 0;                  // a pure stage
intm(x, u) += in(r.x, u);         // an update stage; r.y has become the pure Var u
// f's chosen update stage, rewritten to MERGE the partials:
f(x)  = 0;                        // (pure stage unchanged)
f(x) += intm(x, r.y);             // now reduces over r.y only, reading intm
```

### What `rfactor` builds

Splitting the rule into the two Funcs it produces:

* **The intermediate Func** (named `<orig>_intm`; since names are normalized
  (§10) what matters is that it is *one new distinct Func*). It is a normal
  multi-stage Func:
    * Its **pure stage**'s dimension list is the original Func's pure-stage
      dimensions, followed by the new pure `Var`s in `preserved` order — the new
      vars **outermost**. (Innermost→outermost for `rfactor(r.y, u)`: `[x, u]`.)
    * Its **update stage** is a *copy of the original update stage's dimension
      list* — including any `split`/`reorder`/`tile` already applied to it
      (§9) — with each **preserved** `RVar` replaced in place by its new pure
      `Var`. The **non-preserved** `RVar`s stay as reduction loops (they are the
      reduction the intermediate still performs); the loop order is otherwise
      unchanged. It reads whatever the original update read.
* **The original Func's chosen update stage is rewritten** into the *merge*: its
  dimension list keeps the free `Var`s and the **preserved** `RVar`s (still
  `RVar`s here), and **drops** the non-preserved `RVar`s. Its body now reads the
  intermediate, so **the intermediate becomes a producer of the original Func**
  (it gets a slot in the realization order before `f`, §6).

The preserved `RVar`s thus end up reduced in the *merge* (still `RVar`s in the
original Func) and pure in the *intermediate* (the new `Var`s); the
non-preserved `RVar`s are lifted entirely into the intermediate's reduction.

### `rfactor` rewrites a stage's LHS/RHS — a per-`(specialization, stage)` edit

Read the "rewrite" above as a concrete edit to the definition's **LHS/RHS state**
(§1). `rfactor` **returns a genuinely new Func** — the *intermediate*, a real
pipeline node, **not** a lazily-substituted wrapper like `in`/`clone_in` (§13).
It then rewrites the **`Stage` you called it on** (the *merge*): both that stage's
**left-hand-side index expressions** and its **right-hand-side value expressions**
change. This is a scheduling-directed edit of the algorithm's LHS/RHS that
nonetheless **preserves functional equivalence** (§1): the reduction is
re-associated, not changed.

It is specifically the **RHS** rewrite that makes the intermediate a *producer* of
the original Func — the merge's RHS now reads `intm(...)` (§1: a stage's producers
come from what its expressions read). The **LHS** rewrite instead sets the merge's
output index to the plain pure vars, and never reads the intermediate: a no-op for
an ordinary reduction (whose index already was the pure vars), but real for a
data-dependent scatter — a histogram `g(f(r.x, r.y)) += 1` has its scatter index
`f(...)` moved onto the *intermediate*, leaving the merge's LHS the plain `g(x)`
(so the read of the scattered-over input moves to the intermediate too).

The edit lands on **whichever definition the handle you called `rfactor` on
addresses**, and a handle is specific to a `(specialization, stage)` pair:

* `f.update(n)` addresses update stage *n*'s **base** definition;
* `f.update(n).specialize(cond)` addresses **that branch's own copy** of update
  stage *n*'s definition (§15 — each specialization forks the whole definition,
  LHS/RHS included).

So `rfactor` **composes with `specialize` orthogonally**: applied through a
specialization handle it rewrites **only that branch's** LHS/RHS, leaving the
other branches and the fallback with the definitions they already had. Different
branches then run **different (but functionally equivalent) reduction
algorithms** — the factored branch reads the intermediate (one reduction loop,
over the preserved `RVar`s), the others reduce as before — and, because the
intermediate is only referenced from the branch(es) that were factored, it is
**computed only on the path(s) that use it** (its production is guarded by the
branch condition). This is not a contradiction of "editing the algorithm": the
LHS/RHS is per-branch scheduling state (§1), edited independently per branch and
functional-equivalence-preserving each time.

The common form is `f.update(n).specialize(cond).rfactor(...)` — factor one
branch (e.g. a fast path) while the fallback stays the naive reduction. Other
shapes follow the same rule: `rfactor` **then** `specialize` the returned
intermediate ([examples/rfactor_specialize.cpp](../examples/rfactor_specialize.cpp)
specializes the intermediate's partial-reduction stage), or nesting
`specialize → rfactor → specialize → rfactor` to give several branches their own
factored (or unfactored) reductions.

### Scheduling the intermediate

The intermediate is an ordinary Func returned to you, with its **own default
schedule**. It is non-pure (it has an update), so absent any directive it takes
the **non-pure inline default** (§11): it is realized at its use inside the
merge stage and recomputed for each value of the merge's loops — see
[examples/rfactor_default_inline.cpp](../examples/rfactor_default_inline.cpp). That
defeats the purpose, so you normally schedule it: `intm.compute_root()`
([rfactor_basic.cpp](../examples/rfactor_basic.cpp)) realizes it once before `f`,
and because it is a plain producer of `f` it can also be `compute_at` any loop
of `f` that encloses the merge's use of it
([examples/rfactor_compute_at.cpp](../examples/rfactor_compute_at.cpp)). Its two
stages are scheduled independently, exactly like any Func: `intm` schedules the
pure stage, `intm.update(0)` the partial-reduction stage. So you can
`reorder`/`split`/parallelise the partial reduction on its own
([examples/rfactor_multivar.cpp](../examples/rfactor_multivar.cpp), which preserves
two reduction vars of a 3-D `RDom` and reorders the intermediate's update loops).

### Legality and limits

* `rfactor` may only be called on an **update** stage, never the pure stage —
  the pure stage has no reduction to factor.
* The reduction must be **associative** (and **commutative** too, when an inner
  `RVar` is factored out while an outer one is preserved), or `rfactor` errors.
  Like the `RVar`-reorder rule (§3), this is a property of the update's
  *arithmetic*, which this document does not model mechanically (out of scope,
  as with bounds inference).
* Once the intermediate exists, all the ordinary rules apply to it unchanged:
  its compute/store levels (§6–§8), the legality of a `compute_at` on it (§7),
  and the per-stage transforms on its stages (§9).
* A reduction var may be `split` (§9) *before* being factored — the tiled
  histogram of Halide tutorial lesson 18 does
  `split(r.x, rxo, rxi, …).rfactor({{rxo, u}})`, preserving the outer tile index
  and lifting the inner one. This relies on splitting an `RVar` (whose halves are
  themselves reduction loops), an interaction this document does not yet model;
  it is deferred (see progress.txt). The multi-var example above factors several
  whole `RVar`s of one `RDom` instead, which needs no `RVar` split.

---

