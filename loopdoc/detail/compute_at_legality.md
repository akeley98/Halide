# `compute_at` legality: the full rules

Detail companion to the main [loopdoc.md](../loopdoc.md); section references "§N" point to that document.

The precise conditions under which `f.compute_at(g, v)` is accepted or rejected. The main doc's §7 gives the one-line principle; this is the full account.

---

`f.compute_at(g, v)` is not always legal. First, what a **level** is, because it
is the crux: `(g, v)` is *not* a pointer to one loop. With the stage left
unspecified (§7, "What `(g, var)` points to") it denotes `g`'s `v` loop in **every** stage of
`g` — a whole **family** of loop locations, `g.s0.v`, `g.s1.v`, …, one per stage
that has `v` as a loop variable (§3, §9).
(Halide calls such a `(Func, Var)` pair a *loop level*; the candidate levels at
which `f` may be computed are its legal *sites* — "site" and "level" are the same
kind of object, a `(Func, Var)` pattern, plus the special `root` and `inline`.)
The Func of a level — the one whose loop `f` is placed inside, `g` here — is the
**site func** (Halide's codegen has no dedicated name for it; it is just
`loop_level.func()`). Throughout, "site func" means this Func; bare "site" /
"level" means the `(Func, Var)` location.
`compute_at` gives `f` this single level, and `f` is realized at it in each stage
of `g` that reads `f` — so the several `produce f` blocks share the *same level*
`(g, v)` but sit at *different concrete loops* in the family.

Say the level **encloses** a read of `f` when that read lies inside *some* member
of the family (some `g.s?.v`). The schedule is legal iff the level encloses
**every** read of `f` — i.e. the family of `g.*.v` loops *collectively* covers
them all. So "`v` must enclose every read" never means one loop containing
everything; it means every read sits inside *some* loop of the family. A read in
a **different** consumer Func, or at `g`'s own outer scope, lies inside no
`g.*.v` loop at all, so the level cannot cover it — the usual way to be illegal.

Halide computes this directly: it walks the *whole* loop nest and, at **every
place `f` is read** (including indirectly through other functions `g` consumes),
Halide intersects the stack of `(Func, Var)` levels enclosing that
read; the legal sites are what survive (plus `root`). It is one global
intersection over all reads — you pick a single level, not a different one per
stage. If `(g, v)` does not survive, Halide rejects the schedule with *"Func f is
computed at the following invalid location"* (and lists the legal ones); no loop
nest is produced.

(Choosing a *different* level per read is exactly the freedom the **default
inline** schedule has and a single `compute_at` does not — which is why the
inline default of a non-pure Func cannot, in general, be rewritten as one
`compute_at`; §11.)

**"A read of `f`" is any read in the *realized* loop nest, reached through the
site func's callees — not only reads written literally in the site func's own
definition.** There are two notions of "consumes" and this rule uses the loop-nest
one. To find where `f` is actually read, expand the site func's dependencies
through the funcs it calls: a *pure inline* callee is substituted in, so its
reads of `f` become reads inside the site func; a *realized* callee (one given a
`produce` block — e.g. a Func with an update definition, which cannot be inlined
and defaults to being computed at its consumer's innermost loop, §11) contributes
the reads of `f` inside *its* realization, which sits wherever that callee is
placed. Either way the level must enclose those reads. So `f.compute_at(site, v)`
is legal when `f` is read only by a callee that is itself realized inside the
site's nest — the callee's `produce` block (and the read of `f` within it) lies
inside the chosen `v` loop
([examples/compute_at_inline_dependence.hpp](../examples/compute_at_inline_dependence.hpp):
`p.compute_at(out, y)` is legal in all three of the pure-inline, update-inline,
and `compute_root`-intermediate cases, because in each the intermediate reading
`p` is realized — or inlined — inside `out`'s `y` loop).

The ways `(g, v)` falls outside the surviving set:

* **`v` is missing from a stage that reads `f`.** `v` must name a loop that, *in
  each stage of `g` that reads `f`*, encloses that stage's use — only then does
  every reading stage have somewhere to inject `f`. Two flavors:
    * `v` is not a dimension of `g` at all, so no stage has a loop to inject into
      ([examples/neg_compute_at_bad_var.cpp](../examples/neg_compute_at_bad_var.cpp)).
    * `v` exists in some stages but not in a *reading* one. A reduction `RVar`
      lives only in its own stage, so computing `f` at it is legal when that is
      the **only** stage reading `f`
      ([examples/producer_at_rvar.cpp](../examples/producer_at_rvar.cpp)) but illegal
      when another stage *also* reads `f` and has no such loop
      ([examples/neg_compute_at_update_rvar.cpp](../examples/neg_compute_at_update_rvar.cpp):
      `p` is read by both the pure and the update stage, so the update's `r` loop
      is not a legal site). When the loop *is* shared by every reading stage
      (e.g. a pure `Var` carried through all of them) the site is legal and `f`
      is injected into each
      ([examples/cross_stage_compute_at_shared.cpp](../examples/cross_stage_compute_at_shared.cpp)).

* **`g` is not a consumer.** No stage of `g` reads `f` (directly or through Funcs
  inlined into it), so nothing in `g` needs `f` —
  [examples/neg_compute_at_nonconsumer.cpp](../examples/neg_compute_at_nonconsumer.cpp).

* **`f` is read outside `g`.** Every realization of `f` sits inside `g`, so a use
  of `f` in a *different* Func — or at `g`'s own outer scope — is never reached
  and would read undefined values. The chosen level must enclose those uses too;
  when `f` is read at two unrelated places the only level enclosing both is
  `root`
  ([examples/neg_compute_at_two_consumers.cpp](../examples/neg_compute_at_two_consumers.cpp)).

The last case is the fundamental one: `f` placed inside one site func can only
feed reads within that site func. Feeding consumers that live at different, non-nested
locations is exactly what the wrapper Funcs `in()` / `clone_in()` (§13)
enable; until then such a schedule is simply illegal.

This single principle — *the level must enclose every read of `f`* — is the
whole rule, and it does **not** grow with new features: later directives do not
add compute_at-legality cases, they only **reshape the loop nest** the principle
is evaluated against. `compute_with` (§14) is the example to keep in mind: fusing
`g` into `f` moves `g`'s reads into `f`'s body, so a site that used to enclose
every read of a producer can stop doing so (and a producer computed at a fused
*child* is illegal because the child owns no such loop). Both are this same
"enclose every read" check, re-evaluated on the post-fusion structure — not new
rules.

The illegal cases above are rejected with an error rather than producing a loop
nest; the legal ones cited (`producer_at_rvar`, `cross_stage_compute_at_shared`)
do produce a nest.

---

