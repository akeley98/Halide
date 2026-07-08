# Main agent → human: fresh-eyes re-evaluation (post-compaction)

You asked me to re-evaluate the doc status and the *causes* of the failing
tests. Doing that from scratch changed the picture materially from the previous
"9 reds → 3 fixes" note. **There are 11 reds, and B3's root cause is not what I
previously wrote.** Below: the corrected diagnosis, then direct answers to each
of your five Follow-up tasks.


## The 11 reds and their causes

Four distinct causes (not three). Only **one** is a genuine doc gap; I have
fixed it plus two clarifications. The rest are micro-implementation gaps whose
rules were already (or are now) in loopdoc.md.

| # reds | Examples | Cause | Kind |
|---|---|---|---|
| 3 | `clone_spec_a{0,1,2}_b3` | **(A)** `clone_in({g, hintm})` redirection does not reach the rfactor **intermediate** consumer `hintm`; micro leaves `hintm` reading the original `p`, dragging `p` to root. | micro bug (doc clarified) |
| 4 | `clone_spec_a{0,1,2}_b2`, `clone_in_unused` | **(B)** `in`/`clone_in` pins the wrapper on the *direct caller of `f`* on the path to the named consumer (transitive reach is fine); it errors at lowering if that pinned caller no longer calls `f` — no path (`clone_in_unused`), or `rfactor` after the clone moves `h`'s read into `h_intm` so the pin on `h` goes stale (b2). | **doc gap — FIXED** + micro must reject |
| 3 | `specialize_then_rfactor_each{,_tiled}`, `specialize_tree_rfactor_mix` | **(C)** two rfactor intermediates share the name `g_intm`; micro orders them by the wrong visitation counter (visits the specialization branch before the base/fallback). | micro bug (§6 already documents base-before-branch) |
| 1 | `compute_at_inline_dependence_update_inline` | **(D)** an update-def Func can't inline; it auto-realizes at its consumer's innermost loop, so `p.compute_at(out,y)` above it is legal. Micro rejects it. | micro bug (§11+§7 documented; §7 clarified) |

### Why the previous note was wrong about B3
I had filed B3 as an unrooted "structural mismatch," and lumped it with the
realization-order reds. Fresh probing (`probe/probe_clone_rfactor_intm.cpp`)
shows micro's realization order is *fine*: the minimal DFS-depth case
(`examples/realization_order_dfs.cpp`), a plain clone, and a clone whose consumer
is `compute_at(f)` all order `p` correctly. Only adding the
**rfactor-intermediate-as-clone-consumer** flips `p` to root. So the *symptom* is
realization order (as you spotted), but the *cause* is incomplete clone
redirection.


## Answers to your Follow-up tasks

**1. "B3 root cause is realization order again."** Half right. The observable is
the `f`/`p` order, and your HUMAN_REORDERED log confirms that once `f` precedes
`p` the rest matches. But micro's ordering rule is not the bug — micro would
order `f` before `p` *if* `p` had no consumer inside `f`. The bug is that micro's
`clone_in({g, hintm})` doesn't redirect `hintm`, so `hintm` still reads the
original `p`; that read lives inside `f`, which pulls `p` to root. Fix the
redirection and the order fixes itself. (The residual `produce keep` you saw in
the reordered log is an artifact of the manual reorder; the as-generated micro
already inlines `keep` correctly.)

**2. "Is B3 fixable from current loopdoc.md? Why did micro put `p` before `f`
when `f` < `p`?"** Fixable — it's cause (A), a redirection bug, not a missing
rule. On the `f` vs `p` puzzle: your intuition "`f` < `p` so `f` first" gives the
right answer but for the wrong reason. Realization order is **not** a global sort
by name; it's a **post-order DFS from the output** where the name key only sorts
each Func's *direct-callee list*. `out`'s callee list is `[f, keep]` (`f` <
`keep`), so the walk realizes all of `f`'s subtree before reaching `p` through
`keep` — hence `f` before `p`. (A global name sort would put the leaf `p` first,
which is what a naïve reading of the old §6 text suggested and what your question
was implicitly assuming.) I tightened **§6** to say this explicitly and added
`examples/realization_order_dfs.cpp` (GREEN) as the minimal witness: `a` is
realized *after* `f` despite `"a" < "f"`, because `a` is gated behind `keep`.

**3. "Two notions of consume; is the recursive expansion defined carefully
elsewhere?"** Yes — **§7** already says the compute_at level "must enclose every
place `f` is read (including indirectly through other functions `g` consumes)"
and "reads `f` (directly or through Funcs inlined into it)." That is exactly your
recursive expansion. I added an explicit paragraph naming the two notions and
stating that a read reached through a *realized* callee (e.g. an update-def Func
computed inside the site) counts just like a read through an *inlined* callee —
with a pointer to the `compute_at_inline_dependence` family.

**4. "Investigate `compute_at_inline_dependence_update_inline`."** Cause (D), and
yes it is related to #3. `intm` has an update stage, so it cannot be inlined;
unscheduled it defaults to being computed at `out`'s innermost loop (§11). `p` is
read only by `intm`, whose realization sits inside `out`'s `y` loop, so
`p.compute_at(out, y)` encloses that read → legal. Micro rejects it, so micro is
either trying to inline the update-def Func or rooting it instead of placing it
at the innermost consumer loop. Doc facts already present (§11 placement, §7
enclosing rule); this is a micro fix.

**5. "Is B2 a new in/clone_in rule?"** Yes — new and **not** derivable from the
general realization/compute_at rules. But the rule is subtler than "the named
consumer must call `f`" (my first cut, which the transitive case disproves —
`common.clone_in(c1)` works even though `c1` reaches `common` only through
`maybe_inlined`; probe `probe/probe_clone_c3_sharing.cpp`, wrapper example
`examples/in_but_inlined.hpp`). The actual mechanism (`src/Func.cpp` `resolve_transitive_callers`
+ `src/WrapCalls.cpp`): `in`/`clone_in` walks *down* from each named consumer to
`f` and pins the wrapper onto the **direct caller of `f` on that path**
(transitive reach through intermediates is fine, and the pinned caller can even
be a derived Func such as an rfactor intermediate). At lowering it errors if that
pinned caller no longer directly calls `f`. Two ways: no path exists so the pin
stays on the consumer, which doesn't call `f` (`clone_in_unused`, `g`↛`out`); or
a later directive severs the pinned caller's call — `clone_in({g, h})` pins on
`h`, then `rfactor` moves `h`'s read into `h_intm`, so at lowering `h`'s callee
is `h_intm`, not `f` — a **stale pin**, not a claim `h` can't reach `f` (b2). Fix:
pin on the Func that will call `f` in the final graph — `rfactor` first, then
`clone_in({g, h_intm})` (b3). Documented in **§13** (subsection "The redirected
caller must still call the wrapped Func at lowering"). *(Corrected after you
flagged the clone_in_but_inlined example — since rewritten to wrappers as
in_but_inlined.hpp.)*


## Doc changes made this pass (main agent territory; no micro/harness edits)
- **§6** — realization order reframed as a post-order DFS whose name key sorts
  each callee list, not a global sort; consequence spelled out. New green example
  `examples/realization_order_dfs.cpp`.
- **§13** — new legality subsection: listed consumer must call the wrapped Func
  (covers `clone_in_unused` + B2); redirection reaches rfactor-intermediate
  consumers.
- **§7** — explicit "two notions of consume"/realized-vs-inline callee paragraph;
  `compute_at_inline_dependence` cross-reference.
- Probe `probe/probe_clone_rfactor_intm.cpp` records the B3 isolation.


## What is left for micro-agents (rules now all in loopdoc.md)
- (A) Make `in`/`clone_in` redirection reach rfactor-intermediate consumers → clears B3 ×3.
- (B) Make `in`/`clone_in` **reject** a listed consumer that doesn't call the wrapped Func → clears B2 ×3 + `clone_in_unused` (as negatives).
- (C) Order same-prefix rfactor intermediates by §6 base-before-specialization visitation → clears the 3 visitation reds.
- (D) Realize an update-def Func at its consumer's innermost loop by default, and count reads through it for compute_at legality → clears `compute_at_inline_dependence_update_inline`.
