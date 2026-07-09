Great question — I have direct hands-on evidence on this from this session, so let me give you a concrete answer rather than a vibes-based one.

## Short answer: you're substantially right

"Deterministically by name" is not *wrong*, but it's badly mis-emphasized as the lead. The post-order DFS is indeed the main thing. More precisely, there are **two** places where "by name" oversells, and the section itself has to walk the claim back two paragraphs later ([loopdoc.md:412](loopdoc/loopdoc.md)):

> **The sort key is applied per-consumer, not globally.** … it is the **post-order of a depth-first walk from the output(s)**.

That sentence is the real headline. The fact that it arrives *after* the bold "deterministically by name" claim is the writing problem.

## What actually determines the order (from the implementation)

The realization order is produced by **two separate DFS passes**, and the sort key is a small ingredient inside the second one:

1. **`compute_visit_order`** — a *pre-order* DFS from the output that stamps each Func with an integer `visit_order` on first reach, descending producers in definition order. This is "first-visitation order."
2. **`realization_order`** — a *post-order* DFS from the output. At each node it does literally:
   ```
   sort(this_func's producers, by sort_key);   // sort_key = (name_prefix, visit_order, full_name)
   for p in sorted producers: recurse(p);
   order.push_back(this_func);                  // append AFTER the whole subtree
   ```
   with a shared `visited` set so a shared producer is appended once, on first reach.

So the name only ever sorts **one consumer's direct-callee list**. Everything that makes realization order *realization order* — producer-before-consumer, realize-shared-once, and the counterintuitive cases — comes from the DFS recursion + `visited` set, not from names. The doc's own `realization_order_dfs.cpp` example is the proof: `a` is realized *after* `f` even though `"a" < "f"`, because `a` is only reachable behind `keep` in `out`'s callee list. Name never wins against DFS reachability.

## The sharper point: even the *tie-break* isn't really "by name"

The sort key is `(name_prefix, visit_order, full_name)`. Two of three fields are name-ish, but the operative one in the interesting cases is the middle field — **`visit_order`, which is itself the output of the other structural DFS**, not a name at all.

I know this because the bug I fixed this session (`specialize_then_rfactor_each`) was *entirely* a tie-break bug where name was useless: two rfactor intermediates were **both literally named `g_intm`**, so `name_prefix` tied, `full_name` tied, and the *only* deciding field was `visit_order`. The fix was to change which pass-1 DFS producers get visited first (base-definition reads before specialization-branch reads) — I never touched anything name-related. The doc's `tiebreak_visitation_order.cpp` is the same story: two `b`-prefixed producers, and the *visited-first* one wins even though it's alphabetically later.

So "by name" fails to describe precisely the examples the section builds to exercise its tie-break.

## Verdict on the writing

Technically **correct, rhetorically inverted.** It leads with the least structural, most misinterpretable fact in bold, states it as the positive mechanism ("ordered **by name**"), and then spends the next ~60 lines explaining that actually it's a per-consumer DFS and name is a leaf-level tiebreaker whose own secondary key is a *different* DFS. A reader who stops at the blockquote leaves with a wrong mental model (≈ "sort all Funcs by name").

What "by name" is *genuinely* good for is refuting the real misconception — that source/expression order matters (`g + h` doesn't realize `g` first because it's written first). That refutation is correct and worth keeping.

If I were flagging this as a doc gap, my suggested reframe would be:

- **Lead with the mechanism:** "Realization order is the post-order of a DFS from the output; a shared producer is realized once, ahead of all readers."
- **Demote name to what it is:** "The only freedom is the order a single consumer's independent callees are visited; that's broken by name-prefix, then first-visitation order, then full name — it does *not* reorder anything globally."
- **Flag `visit_order` as structural** right where the sort key is introduced (line 408), not 45 lines later, so nobody reads the three-field key as "name, name-ish, name."

Net: your instinct is correct — the DFS is the engine, and name is a small, sometimes-irrelevant tiebreaker riding on top of it. The bold framing gives it top billing it doesn't earn.
