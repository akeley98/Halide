# Main agent → human: open items & decisions

Status of the adversarial-example batch from `human_to_main_agent.md`. All four
tasks have been written (per your "work on all; keep REDs visible" instruction).
Harness state summarised at the bottom.


## Task status

- [x] **Task 1 — rfactor before specialize** (family `rfactor_then_specialize_impl.hpp`).
      Both members GREEN. member 1 (no tile): both specialize branches alias the
      one pre-fork intermediate. member 2 (tile the reduction, rfactor inner):
      GREEN after your split/fuse is_rvar fix (a09d33c6d) + one collapse (the
      u-outer tile loop iuo has extent 1 when u's extent == the tile factor).
      DECIDE #1 (RVar-split) is RESOLVED.

- [x] **Task 2 — specialize then rfactor each branch** (family
      `specialize_then_rfactor_each_impl.hpp`): both members **RED** — the §6
      base-before-specialization visitation-order gap (two intermediates print as
      `g_intm`; micro orders them wrong). §6 doc already fixed; micro fix pending.

- [x] **Task 3 — specialize tree × rfactor before/after × tile**
      (`specialize_tree_rfactor_mix.cpp`): **RED** — same §6 visitation-order gap,
      in a tree + tile context.

- [x] **Task 4 — clone_in × specialize matrix** (family
      `clone_specialize_matrix_impl.hpp`): full 15-combo matrix committed (5
      choiceB × 3 choiceA). ANSWER to your question: rfactor-h-FIRST then
      clone_in({g, h_intm}) has NO scheduling-order contradiction — legal for all
      choiceA. State:
      * B0 compute_with — GREEN ×3.
      * B1 compute_at f — GREEN ×3.
      * B4 invalid pc location — NEGATIVE ×3.
      * B2 clone_in({g,h}) then rfactor(h) — **RED ×3**: real Halide errors (the
        wrap can't follow into h_intm), but micro ACCEPTS it → micro gap
        (clone-wrap-vs-rfactor legality not enforced).
      * B3 rfactor(h) first + clone_in({g, h_intm}) (the corrected positive) —
        **RED ×3**: structural mismatch in the clone + rfactor-intermediate +
        compute_at chain (realization-order / clone-of-rfactor-output handling).


## RED scaffolds (kept visible per your instruction; each maps to a micro fix)

All reds now cluster into THREE micro fixes:

1. **§6 visitation order** — realization order must visit a stage's
   base-definition producers before its specialization-branch producers (loopdoc
   §6, already documented; `DefinitionContents::accept` order). Clears:
   `specialize_then_rfactor_each`, `specialize_then_rfactor_each_tiled`,
   `specialize_tree_rfactor_mix`. → micro-agent task.
2. **clone-wrap-vs-rfactor legality** — micro must REJECT `clone_in({g,h})` then
   `rfactor(h)` (h no longer calls the wrapped Func), which real Halide errors on
   but micro currently accepts. Clears the B2 reds
   (`clone_spec_a{0,1,2}_b2`) as proper negatives. → micro-agent task.
3. **clone-of-rfactor-output + compute_at chain** — the B3 positives
   (`clone_spec_a{0,1,2}_b3`, rfactor(h) first + `clone_in({g, h_intm})`) mismatch
   structurally; needs diagnosis of the realization-order / cloned-intermediate
   handling. → micro-agent task (a fresh find, not yet root-caused).

(The RVar-split red is GONE — Task 1 tiled is green after your split/fuse fix.)


## Decisions — both resolved

- **[DECIDE #1] RVar-split:** RESOLVED by your split/fuse `is_rvar` fix + one
  collapse annotation. No action needed.
- **[DECIDE #2] Task 4 B2/B3 shape:** RESOLVED by your task update — B2 is the
  clone-then-rfactor NEGATIVE (documents the wrap-breaks constraint) and B3 is the
  corrected `clone_in({g, h_intm})` positive. Both committed; their reds are micro
  fixes (#2, #3 above), not design questions.


## Harness state
9 intentional REDs: 3 visitation-order (Tasks 2/3), 3 B2 (micro doesn't reject),
3 B3 (structural). Three micro fixes clear them. Everything else green/negative,
including all of Task 1 and Task 4 B0/B1/B4.
