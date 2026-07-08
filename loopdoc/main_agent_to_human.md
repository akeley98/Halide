# Main agent → human: open items & decisions

Status of the adversarial-example batch from `human_to_main_agent.md`. All four
tasks have been written (per your "work on all; keep REDs visible" instruction).
Harness state summarised at the bottom.


## Task status

- [x] **Task 1 — rfactor before specialize** (family `rfactor_then_specialize_impl.hpp`).
      * member 1 (no tile): `rfactor_then_specialize.cpp` — GREEN. Both specialize
        branches alias the one pre-fork intermediate.
      * member 2 (tile the reduction, rfactor inner): `rfactor_then_specialize_tiled.cpp`
        — **RED**. Reveals that `split()` doesn't set `is_rvar` on the halves
        (your `DimData::_is_rvar` refactor is in place, but split/fuse/tile still
        build the produced dims with `is_rvar=false`), so rfactor's merge-drop
        misfires. See DECIDE #1.

- [x] **Task 2 — specialize then rfactor each branch** (family
      `specialize_then_rfactor_each_impl.hpp`): both members **RED** — the §6
      base-before-specialization visitation-order gap (two intermediates print as
      `g_intm`; micro orders them wrong). §6 doc already fixed; micro fix pending.

- [x] **Task 3 — specialize tree × rfactor before/after × tile**
      (`specialize_tree_rfactor_mix.cpp`): **RED** — same §6 visitation-order gap,
      in a tree + tile context.

- [x] **Task 4 — clone_in × specialize matrix** (family
      `clone_specialize_matrix_impl.hpp`): 9 of 12 combos committed, ALL PASSING.
      * choiceA {p-before-clone, p-after-clone, clone} × choiceB0 (compute_with):
        GREEN ×3.
      * choiceA × choiceB1 (g,h,clone compute_at f.y): GREEN ×3 (after declaring
        the g/h y-elision).
      * choiceA × choiceB3 (illegal pc location): NEGATIVE ×3 (both backends
        reject).
      * choiceB2 (rfactor output consumes the clone): **FLAGGED, not committed** —
        see the finding below.


## RED scaffolds (kept visible per your instruction; each has a documented fix)

1. `rfactor_then_specialize_tiled` — RVar-split: `split` must propagate `is_rvar`.
2. `specialize_then_rfactor_each` + `_tiled` — §6 visitation order.
3. `specialize_tree_rfactor_mix` — §6 visitation order.

They cluster into just TWO micro fixes (see decisions), so the RED count is
larger than the amount of work to clear them.


## Findings surfaced by this batch

- **`clone_in` wrap does not follow `rfactor` (Task 4 B2).** `p.clone_in({g,h})`
  then `h.update(0).rfactor(...)` errors "h does not call p": the rfactor moves
  h's read of the clone into the intermediate `hintm`, so `h` no longer calls the
  wrapped Func and the wrap can't attach. To make the rfactor output consume the
  clone you must clone AFTER rfactor, targeting `{g, hintm}` — which conflicts
  with this family's clone-then-schedule order.
  **[DECIDE #2] Task 4 B2:** (a) leave it flagged/omitted; (b) I add a separate
  mini-example doing clone-after-rfactor (`{g, hintm}`) as the "working" form; or
  (c) commit clone-then-rfactor as a NEGATIVE documenting the ordering constraint.
  *(Recommendation: (b) — a small positive that shows the supported ordering.)*


## Decisions

- **[DECIDE #1] RVar-split (`split` propagate `is_rvar`).** Now a small, specific
  micro fix: `split`/`fuse`/`tile` in `micro_halide.hpp` must set `is_rvar` on the
  produced `DimData` from the `VarOrRVar` kind (they currently default it to
  false). This flips `rfactor_then_specialize_tiled` (and unblocks all
  "tile-the-reduction + rfactor-inner" variants) to green. Trivial enough that
  you may prefer to do it directly (as with the `DimData` refactor), or hand it to
  a micro-agent. *(Not done by me — it's micro impl, and you asked to see state.)*

- **§6 visitation-order fix (Tasks 2 & 3).** Doc-derivable micro fix: realization
  order must visit a stage's base-definition producers before its
  specialization-branch producers (loopdoc §6, now documented; `DefinitionContents::accept`
  order). Flips the 3 visitation-order REDs to green. → a micro-agent task.

- **[DECIDE #2]** above (Task 4 B2 shape).


## Harness state
Reds are the 4 scaffolds above (Task 1 tiled + Task 2 ×2 + Task 3). Two micro
fixes (RVar-split `is_rvar` propagation; §6 base-before-specialization visitation)
clear all of them. Task 4's 9 committed combos are green/negative.
