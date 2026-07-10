# loopdoc.md full read-through — findings (main agent)

Read front-to-back on 2026-07-10. No edits made. Overall: the document is
accurate and internally consistent; nothing in it contradicts what I know of
Halide's actual behavior, and every claim I spot-checked this session (clone
inherits `f`'s schedule, compute_with for_type match, realization-order DFS +
edge-label tie-break, the `device_api==None` extent-1 gate) holds. The items
below are mostly clarity, not correctness. Ranked by how much they'd trip a
reader.

## Worth fixing

1. **§1, "Objects and their conceptual state" — dangling antecedent (I introduced
   this).** In the Func's stages bullet, the new loop-type sentences sit between
   "Each stage has its own ordered list of **loop dimensions** …" and "It also has
   **its own left-hand-side index expressions** …". After two sentences whose
   subject is "Each dimension" ("Each dimension also carries a **loop type** …"),
   the "It also has its own left-hand-side…" reads as if the *dimension* has the
   LHS/RHS — but "It" means the *stage*. A reader could conclude dimensions carry
   LHS/RHS. Fix: make the referent explicit, e.g. start that clause "Each stage
   also has its own left-hand-side…", or move the per-dimension type/device
   sentence to the end of the bullet so the stage's own attributes stay together.

   HUMAN: either proposed fix approved

2. **§7, line ~837 — reader-facing "milestone" jargon.** "…is exactly what the
   wrapper Funcs `in()` / `clone_in()` (a later milestone) enable…". This is the
   only remaining "milestone" in the doc (the §9 one was retargeted to §17
   earlier). Everywhere else the doc cross-references by section; "a later
   milestone" is development-process vocabulary that means nothing to a reader.
   Fix: "(§13)".

   HUMAN: fix approved

## Minor / optional

3. **§13, `clone_in` subsection vs. its opening — timing caveat only stated once.**
   The section opening (fixed today) correctly says a clone copies "`f`'s *current*
   schedule … default only if `f` is still unscheduled when you clone." The
   `### f.clone_in(g)` subsection just below still states it flatly: "a **copy of
   `f`'s entire definition** (all stages, schedule, and specializations)" with no
   "as of the call" qualifier. Not wrong (the opening governs), but a reader who
   jumps straight to the subsection misses the timing nuance. Optional: add "as it
   stands when you call `clone_in`" to the subsection, or a back-reference to the
   opening.

   HUMAN: small enough to be worth repeating as a special case; add the clause again instead of an intro back-reference please.

4. **§7, line ~608 — "undecidable in general" is a slight overstatement.**
   "Predicting exactly which dimensions collapse requires bounds inference, which
   is undecidable in general…". Halide *does* compute required-region extents
   (interval arithmetic); the honest point is that it depends on arbitrary index
   arithmetic and is out of scope for micro, not that it is formally undecidable.
   Consider softening to "not something this document derives" / "requires bounds
   inference micro does not model" to match the careful hedging used elsewhere
   (e.g. the associativity notes in §3/§12 say "out of scope," not "undecidable").

    HUMAN: yes, we can just drop this claim

5. **§14, line ~1784 — broken inline-code span (cosmetic).** The sentence
   "…the top-level body runs `f.s0, f.s1, g.s0, h.s0`(with `g.s1, f.s2` spliced
   in)`, h.s1, h.s2, g.s2`." closes the code span before "(with…)" and reopens it,
   so it renders as two spans with prose between and a stray leading comma in the
   second. Reflow as one span or split the parenthetical out cleanly.

   HUMAN: Either fix approved

6. **§15 "Legality" — discoverability.** The rule "the Func that calls
   `compute_with` must have no specializations" lives only in §15, and §14's own
   Legality list does not mention or cross-reference it. A reader auditing
   compute_with legality from §14 wouldn't find this constraint. Optional: add a
   one-line pointer from §14 Legality to §15.

   HUMAN: fix approved

## Surprises checked and found consistent (no action)

- §6 realization order as a post-order DFS with a per-consumer edge-label
  tie-break (prefix → first-visitation index → full name), and fused groups as a
  single contracted multigraph vertex with preserved in-edge labels — matches
  `RealizationOrder.cpp`/`find_fused_groups` as I understand them.
- §12 rfactor's LHS/RHS rewrite (incl. the histogram scatter-index moving to the
  intermediate) and the per-(specialization, stage) edit routing.
- §13 the two-phase eager/deferred model, the pin-set collision order-sensitivity,
  the blind-to-pending-rewrites case, and the clone-once `#6476` limitation.
- §17 factor-form asymmetry (vectorize/unroll → inner, parallel → outer), type
  riding the dimension through split/fuse/reorder, and the extent-1/device gate.

None of these read as wrong; I note them only to record that the surprising
claims were the ones I actively tried to falsify and could not.
