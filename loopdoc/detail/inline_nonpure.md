# Inlined non-pure Funcs (the deferred default)

Detail companion to the main [loopdoc.md](../loopdoc.md); section references "§N" point to that document.

A **non-pure** Func left at the default `inline` level cannot be textually substituted, so Halide realizes it at the innermost loop enclosing *each* use — equal to a per-use `compute_at` only when every use shares a depth.

---

§§5–10 assumed every inline Func is pure, and therefore non-realized (§4). This
section handles the one leftover case: a **non-pure** Func (one with update
definitions, §3) left at the default **inline** level. It is uncommon — it is
probably not the fastest choice — so it is isolated here rather than woven
through the earlier sections.

A non-pure Func cannot be textually substituted (a reduction is not an
expression), so the inline level **realizes** it — but at the latest, innermost
place possible: Halide materializes it *at each use, just inside the innermost
loop enclosing that use*, recomputing it from scratch every iteration (Halide's
`compute_inline()` doc: a Func with an update definition, left inline, "gets
computed as close to the innermost loop as possible"). For `g(x, y) = f(x)` with
`f` an unscheduled reduction (see
[examples/update_default_inline.cpp](../examples/update_default_inline.cpp); and
[examples/weird_histogram_sampling.cpp](../examples/weird_histogram_sampling.cpp),
a histogram feeding a pointwise consumer):

```
produce g:
  for y:
    for x:
      produce f:          # all of f's stages, recomputed every (x, y)
        f(...) = ...
        for r:
          f(...) = ...
      consume f:
        g(...) = ...
```

### When it equals a `compute_at`, and when it does not

If `f` is read at a **single** loop depth, this default is *exactly*
`f.compute_at(consumer, v)` with `v` the innermost loop enclosing the use — the
nests are byte-identical. Both examples above are this common case, which is why
the rest of the document can treat the non-pure default as "a default
`compute_at` at the innermost use" and lose nothing.

It stops being expressible as **any** single `compute_at` once `f` is read at
*different depths in different stages* of a consumer, because the inline level
places `f` at each use's *own* innermost enclosing loop, **independently per
use**, whereas `compute_at(consumer, v)` can name only one loop. Take non-pure
`f` read as `f(x)` in a consumer's pure stage (depth `x`) and as `f(r)` inside
its update stage's reduction (depth `r`):

* the inline default puts `f` inside `x` in stage 0 **and** inside `r` in stage 1;
* `compute_at(consumer, x)` is too shallow for stage 1 (it puts `f` at `x` there,
  not inside `r`);
* `compute_at(consumer, r)` is **illegal** — `r` exists only in the update stage,
  so it does not enclose the pure stage's use of `f` (§7's legal-site rule).

So inline-of-non-pure is genuinely *more* than "a default `compute_at`": it is a
per-use-site materialization, equal to a `compute_at` only when every use shares
a depth. This is the deepest reason "inline" is not just a compute level with one
site (§4's wart): for a non-pure Func it means "recompute at the innermost point
of *each* use." (So you must predict it per use site, not by rewriting `f` to a
single `compute_at`.)

---

