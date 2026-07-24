Reviewed [tasks_2026-07-22.md](~/Halide/dendritic_hl/tasks_2026-07-22.md). Overall it's a strong, self-aware sketch — most of the session's threads are not just acknowledged but engaged thoughtfully. Quick coverage check, then the gaps (the part you asked about), then the two "be critical" sections.

## Session issues that ARE acknowledged (well)
- **`canon` overload / verdict-vs-implemented** → Commentary `review` enum + the explicit note: "make an unchanged schedule canonical and give it a negative review… Document the exception to the 'canonicalize when implemented' rule." That's exactly the `Y_root_vectorized` incident, resolved cleanly.
- **Beam-vs-tree / persistent frontier** → private idea list + top-ideas tool + cost scoring, with the deliberate "not beam search, not level-bucketed" stance.
- **Warning time-vampire / annotate-don't-silence** → Warning Toggles, and "hide really means replace with a note citing the toggle + commentary" is precisely the degrade-to-annotation pattern.
- **Recombination/grafting** → `borrowed` side links.
- **Comparison campaign** (interleaving, `wall_time_min`, `profiler_version` gating, benchmark sets) → covered.
- **Func-name (non-)identity / `$` suffix** → flagged in the JSON section.

## Issues NOT (or under-) acknowledged

1. **Statistical significance is missing from the cost model — this is the session's central lesson, and the doc reverts to point estimates.** Cost = "lowest median `wall_time_min`," and obsolete-tagging fires whenever a child has "a lower cost" — with **no significance margin**. We spent the session proving that ~1% differences are noise unless paired-tested, and we built `wall_time_smallest`/`tail_spread` precisely as an error bar on the min. None of that is used: median-min collapses it to a scalar and compares with `<`. Consequence: obsolete-tagging will **thrash on sub-noise differences** (a schedule 0.3% "faster" by luck silently obsoletes a real idea). At minimum the cost comparison needs a "significantly lower" test (e.g. difference must exceed the `tail_spread`/CI), not bare `<`. This is the biggest gap.

2. **No machine/hostname comparability gate.** Cost filters by benchmark-set reachability + `profiler_version`, but not `hostname`/`cpu_count`. Pooling `wall_time_min` across machines is meaningless. Either gate on machine identity or explicitly document "a benchmark set is assumed single-machine." We raised comparability repeatedly (it's why `profiler_version` exists); machine identity is the other half.

3. **`max_warnings = 256` silently truncates** — a latent profiler cap I hit while implementing the warnings smuggle. The JSONL warnings dump reuses that same capped array, so a big pipeline's warnings can be silently dropped. Minor for our sizes, but since programmatic warning-filtering depends on the list being complete, it's worth a "known limitation" line (or a `warnings_truncated` flag).

4. **Minor / clarifications:**
   - The "Profiler Sampling" section drops *phantom time* citing Adams, but **survivorship bias was the distinct, more damaging billing issue**. It's fine to drop *both* — but only because the campaign uses `wall_time_min` (direct measurement, bypassing sampling entirely), not because the bias doesn't exist. Worth one line so a future reader knows *why* it's safe to ignore.
   - The WarningToggle key `(warning kind, function name)` silently inherits the func-name uniqueness caveat flagged in the JSON section — a cross-reference would prevent someone treating the pair as a stable key.

## The two "be critical" sections

**Cost Scoring** — two real problems beyond the significance gap (#1):
- **The `cost = 0` sentinel conflates "unbenchmarked" with "fastest."** Since lower is better and 0 is the floor, an *implemented-but-unbenchmarked* child schedule (empty benchmark list → cost 0) will **obsolete its benchmarked parent** in the obsolete-tagging pass. (The unimplemented-child case is safe — it inherits the parent's cost — but the implemented-unbenchmarked one is not.) You want "unbenchmarked = unknown/excluded from comparisons," which is a *different* state from "benchmarked at 0." Using the same 0 for "float to top of the sort" and "compare in obsolete-tagging" is the conflation. Consider a distinct sentinel (or: unbenchmarked floats in the sort but is skipped as an obsoleter).
- "Report the best generator parameters somehow" (TODO) is load-bearing and currently unspecified — the cost is defined as the min over parameter sets, so the winning parameters must travel with the cost or the number isn't actionable.

**Private Idea List** — largely sound; the pool-tag/obsolete-tag design is a nice organic-pruning idea. Concerns:
- Inherits the significance (#1) and cost-0 (above) issues, since obsolete-tagging is defined here.
- "Obsolete tagging checks child ideas of the canonical schedule" is a purely *local* (parent→child) check — which is consistent with your "organic, not global" philosophy, but it means an idea made redundant by a better idea in a *sibling* subtree is never tagged. That's a deliberate trade (same one as dropping global warning scope), worth stating as intentional so it doesn't read as an oversight.

## One affirmation worth recording
Your Warning-Toggles self-critique ("schedule-scoped toggles are DOA because they expire when the schedule changes") is slightly *too* pessimistic in a good way: because toggles aggregate along the **path to root**, a toggle on schedule S covers S *and all its descendants* — so it does **not** expire when you make a child schedule; the child inherits it. The real limitation is only cross-*branch* reuse (siblings), which is exactly the algorithm-intrinsic case (e.g. the scatter `no_vector_ops`) that wants global scope. So your "defer global, retrofit if the finite rediscovery cost proves annoying" call is well-targeted — the durable subtree behavior you already get from path-aggregation covers more than the note gives it credit for.
