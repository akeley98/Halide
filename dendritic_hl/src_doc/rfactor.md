# rfactor

_Part of the [src_doc set](README.md); sections keep their global numbers (§1–§14), and cross-file references are written as "§N"._

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
  before it, §3).

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
