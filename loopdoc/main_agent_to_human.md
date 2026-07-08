# Main agent → human: open items & decisions

Status of the adversarial-example batch requested in `human_to_main_agent.md`
(the "scheduling metaprogramming is not pure-functional / aliased-vs-forked
state" families). Working tree is clean; harness is green except where noted.

Decisions are marked **[DECIDE]**. Nothing below is blocking a green harness — the
held items were deliberately *not* committed so the harness stays green until you
choose a direction.


## DONE

- [x] **Task 1, member 1 — rfactor before specialize** (commit f49f546ea).
      `examples/rfactor_then_specialize.cpp` (+ `_impl.hpp`). GREEN (micro==real).
      Tests the aliasing case: rfactor runs on the base BEFORE the specialize
      fork, so both branches alias the ONE scheduled intermediate. This is the
      on-target "shared scheduling state" test and it passes.


## HELD (not committed; deliberate, pending a decision)

- [ ] **Task 1, member 2 — tile the reduction then rfactor the inner half**
      (`rfactor_then_specialize_tiled`, the `tile_reduction=true` branch of the
      committed `.hpp`).
      * Verified RED against real Halide, but ONLY because it hits the deferred
        **RVar-split gap** (`progress.txt` `[open] rfactor`): micro's RVar
        tracking is not updated by `split`, so rfactor's merge-drop can't
        recognise the split halves and emits an extra reduction loop.
      * This is about RVar tracking, NOT the state-aliasing this family targets.
      * The `.hpp` keeps the parameter + a note so this member is one `.cpp` away
        once RVar-split is tackled.
      * **[DECIDE #1] RVar-split gap:** reopen it now (a real chunk of micro work
        — teach `split` to track RVar-ness so the split halves are recognised as
        RVars), which would unblock every "tile the reduction + rfactor inner"
        variant (this, and Task 3's "tile")? Or keep it deferred and skip those
        variants for now?  *(Recommendation: keep deferred — it tests RVar
        tracking, not the aliasing question this batch is about.)*

- [ ] **Task 2 — specialize then rfactor EACH branch** (built + diagnosed, files
      removed so the harness stays clean).
      * Intent: two branches each rfactor'd into their OWN intermediate, scheduled
        differently → tests that branch/base handles route to DISTINCT, non-aliased
        state.
      * Finding: micro gets the STATE right — it makes two distinct intermediates
        and applies the split to the correct one (no aliasing bug). BUT both
        rfactor intermediates print as `g_intm` (rfactor names every intermediate
        `<orig>_intm`), so the name-based canonicalizer merges them into one id and
        cannot tell them apart. The only residual signal is their REALIZATION
        ORDER, which micro orders opposite to real Halide. So the example is red
        for a same-name + §6-tie-break reason, not the state question it targets.
      * Because rfactor auto-names both intermediates identically, ANY "rfactor
        each branch of one Func" example inherently hits this.
      * **[DECIDE #2] Task 2 approach:** (a) treat as an ill-posed test / known
        canonicalizer limitation (funcs identified by printed name) and skip;
        (b) I dig into Halide's realization-order tie-break for same-named funcs,
        document it in §6, and make micro match; or (c) reshape the example to
        avoid two same-named intermediates (hard — can't easily rename rfactor
        outputs).  *(Recommendation: (a) — document the canonicalizer limitation;
        micro's actual state handling is correct here.)*


## NOT STARTED

- [ ] **Task 3 — mix of Tasks 1&2 + non-trivial specialize tree + tile**
      ("rfactor before some specialize branches and after others; tile at some
      point"). The "tile" almost certainly re-hits the RVar-split gap; the
      rfactor-each-branch part re-hits the Task-2 same-name issue. Blocked on
      DECIDE #1 and #2.

- [ ] **Task 4 — clone_in + specialize matrix.** The biggest single item:
      choice A (3 options) × choice B (4 options) = up to **12 examples**, each a
      2-Func `clone_in({g, h})` (g pure, h uses the clone in an update) combined
      with a compute_with / compute_at / rfactor / illegal structure, with the
      original `this`-of-`clone_in` also non-trivially used. Largely independent
      of the rfactor tie-break issues above (clones are named `<orig>_clone_in`,
      distinct from the original, so the same-name problem likely does NOT bite).
      * **[DECIDE #3] Task 4 scope:** generate the full 12-combo matrix, or a
        representative subset (~4–5) first for structure review before expanding?
        *(Recommendation: subset first, confirm structure, then expand.)*


## Cross-cutting note

The tile/RVar-split variants and the Task-2 same-name issue are the two recurring
blockers; Task 4 is mostly orthogonal to both and is where the most new,
green-able coverage likely lives. Pre-existing deferred `[open]` gaps unrelated to
this batch (per-branch elision, compute_with dim-KIND, and the RVar-split gap
itself) are tracked in `progress.txt`.


## Suggested order once you decide
1. DECIDE #3 → do Task 4 subset (highest independent value).
2. DECIDE #2 → resolve Task 2 (likely document-and-skip).
3. DECIDE #1 → if reopening RVar-split, do it, then Task 1 member 2 + Task 3.
