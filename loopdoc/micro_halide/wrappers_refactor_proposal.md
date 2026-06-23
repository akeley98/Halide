# micro_halide refactor proposal: prepare for in() / clone_in()

Goal: a **functionality-neutral** refactor (zero behavior change, all 91 tests
stay green) that creates a single narrow seam where wrapper redirection can later
be inserted — so the wrappers-milestone micro-agent edits a couple of clearly
marked functions instead of tree-searching the whole builder for
`shared_ptr<FuncContents>` to rewire.

## Guiding principle (from src_doc §13)

Halide does **not** mutate consumers at `in()` time. `f.in(g)` records
`{consumer → wrapper}` on the **wrapped** Func and applies the call-substitution
as a *derived* pass (`wrap_func_calls`) when the nest is built, on the env copy.
So in micro_halide the wrapper redirection should be a **read-time resolution of
producers**, never an **edit of consumer state**. That is the entire idea: the
"treasure hunt" only exists if you try to eagerly rewrite consumers; mirror
Halide and it disappears.

## Where producers are read in the builder today (the seam map)

Two kinds of producer reads during nest construction:

1. **Per-stage** — already funnelled through ONE accessor:
   `stage_producers(FuncContents* f, int s)` (returns `f->stages[s].producers`).
   Everything per-stage (`stage_reads` → `body_uses` / `g_uses_f` / legality /
   injection / store-node placement) goes through it. ✓ already a choke point.

2. **Whole-func** — three *direct* `f->producers` reads, not yet centralized:
   - `compute_visit_order` (realization-order tie-break visitation DFS)
   - `realization_order` (the producers-before-consumers DFS)
   - `inlined_reads` (chasing inlined producer chains)

Producer *construction* at definition time (`collect_producers`,
`record_update`, `rfactor`) is separate and must **not** change — mirroring
Halide, `in()` does not touch consumer definitions.

## Step 1 — the neutral refactor (do now, pending approval)

Add one whole-func accessor mirroring the per-stage one, and route the three
direct reads through it. Both accessors are **identity today** (return the stored
list), so behavior is unchanged.

```cpp
// THE WRAPPER-RESOLUTION SEAM. All producer reads during nest construction go
// through these two accessors, keyed by the CONSUMER `f`. Today they return the
// stored lists verbatim. The in()/clone_in() milestone inserts wrapper
// redirection HERE (and only here): for consumer `f`, a producer that has a
// wrapper registered for `f` is swapped for that wrapper. Nothing else in the
// builder changes.
const std::vector<std::shared_ptr<FuncContents>>&
func_producers(FuncContents* f) { return f->producers; }

const std::vector<std::shared_ptr<FuncContents>>&
stage_producers(FuncContents* f, int s) { return f->stages[s].producers; } // exists
```

- Replace `f->producers` with `func_producers(f)` in `compute_visit_order`,
  `realization_order`, `inlined_reads`.
- Add the comment block above both accessors.
- `sh test.sh` must still report all 91 passing (pure indirection). Commit as a
  standalone "functionality-neutral" change so the diff is obviously inert.

That is the whole neutral refactor. No new data, no API, no behavior.

## Step 2 — what the wrappers milestone then adds (plan, NOT done now)

Sketched so the seam above is justified; this is the micro-agent's later work,
driven by the loopdoc wrappers section.

1. **Data** — one field on `FuncContents`, mirroring Halide's
   `func_schedule.wrappers()`:
   ```cpp
   std::map<std::string, std::shared_ptr<FuncContents>> wrappers; // consumer name -> wrapper; "" = global
   ```
2. **API** — `Func::in(const Func&)`, `in(vector)`, `in()`, `clone_in(...)`:
   build the wrapper Func (`in` = a pure one-stage Func whose producers are
   `{ wrapped }`; `clone_in` = a copy of the wrapped Func's stages/dims/producers
   under a new name), then record it in the **wrapped** Func's `wrappers` map.
   **Do not touch the consumers.**
3. **Resolution** — one pass at the start of nest construction (the micro analog
   of `wrap_func_calls`): for each consumer, build its resolved producer lists by
   swapping any producer that has a wrapper registered for that consumer (custom
   key = consumer name; `""` = global, applied to all but the wrapped Func and its
   own wrappers, custom taking precedence). Store the resolved lists; the two
   accessors return from them. Pointer identity stays stable (the wrapper/wrapped
   `FuncContents` are the canonical ones), so all `p.get() == f` comparisons keep
   working.
4. **Everything else "just works."** Wrappers now appear in resolved producer
   sets, so `realization_order` visits wrapped → wrapper → consumer, and
   `stage_reads` sees the wrapper as the consumer's producer — injection,
   legality, and store-node placement treat it as an ordinary Func. No edits to
   those.

## Why this kills the churn

The wrappers milestone touches: `FuncContents` (+1 field), the new `in/clone_in`
methods, the one resolution pass, and the two accessors. It does **not** touch
`realization_order`, `body_uses`, injection, legality, store-node placement, or
any consumer's stored producers. Consumer redirection is centralized in the
accessor resolution instead of scattered across the builder.

## Open questions to settle before Step 2 (not blocking Step 1)

- **Transitive callers**: Halide's `resolve_transitive_callers` rewrites `f.in(h)`
  (where `h` reaches `f` only via `g`) to register under `g`. Implement, or
  document as a deliberate simplification/gap?
- **Realization-order tie-break**: wrappers share a name *prefix* with the wrapped
  Func, which finally exercises the so-far-untested secondary key (visitation
  order); see progress.txt. A wrapper example should target this.
- **Resolution timing**: one-time precompute (recommended, mirrors Halide) vs.
  lazy per-accessor-call. One-time keeps it cheap and identity-stable.
