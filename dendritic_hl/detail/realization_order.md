# Realization order in detail

Detail companion to the main [loopdoc.md](../loopdoc.md); section references "§N" point to that document.

The precise order in which the `compute_root` Funcs (and every other realized Func) are emitted: the depth-first walk, its tie-break, the first-visitation index, and how a fused group fits in. The main doc's §6 introduces realization order as a topological sort of the producer/consumer graph; this is the full account.

---

Picture the pipeline as a directed graph: **nodes are Funcs**, and each node has
an **out-edge to every Func it reads** (consumer → producer). Every Func is a
node, including those that end up inlined — inlining is decided later (§5), and
keeping an inline Func as a node is what lets it **transmit dependencies** (inline
`b` reading rooted `a` keeps `a` ahead of everything that reads `b`; in
`box_blur`, `output` inlines `blur_y`→`blur_x`→`input_16`, so `input_16` precedes
`output`).

**The order is the post-order of a depth-first walk from the output(s)**, with
one shared *visited* set: at each node, descend its out-edges, then append the
node *after* its whole subtree; a node already visited is skipped. Two properties
fall straight out:

- a producer is appended before the consumer that descended into it, so **every
  producer precedes all its consumers**;
- a shared producer is reached by several paths but appended **once**, on the
  first — **realized once, ahead of every reader**
  ([examples/diamond_root.cpp](../examples/diamond_root.cpp)). (Reaching a node that
  is visited but not yet appended is a back-edge = dependency cycle = error.)

The name never reorders the graph globally. A producer reachable only through an
alphabetically-*later* sibling realizes *after* that sibling's whole subtree even
if its own name sorts earlier:
[examples/realization_order_dfs.cpp](../examples/realization_order_dfs.cpp) yields
`mid, f, a, out` — **`a` after `f` despite `"a" < "f"`** — because `a` is
reachable only behind `keep` in `out`'s out-edges.

#### The one degree of freedom: the order of a node's out-edges

The walk's only choice is the order in which it descends a node's out-edges (its
independent producers). Give each edge a **label** — the key of the producer it
points at — and the walk descends a node's out-edges in **label order**. The label
is:

> **prefix**, then **first-visitation index**, then **full name** — where the
> prefix is the name with any `$n` uniqueness suffix and trailing digits removed.
> The first-visitation index is **unique per Func**, so it always settles a prefix
> tie; the third field, full name, is only a deterministic total-order fallback
> and is never actually the deciding field.

This ranks only *one consumer's* producers; it is not a global sort, and not the
left-to-right order of the defining expression
([examples/tiebreak_realization_order.cpp](../examples/tiebreak_realization_order.cpp):
`a2d` before `b1d` though written `b1d(x) + a2d(x, y)`). For an ordinary edge the
label is just the target Func's key, so you can think "sort by target"; keeping it
as an edge *label* rather than a property of the target vertex matters only for
`compute_with`, below.

The middle field, **first-visitation index**, is a structural stamp (a separate
pre-order DFS, detailed next) — *not* a name. It is the field that actually
settles ties: when two producers share a prefix
([examples/tiebreak_visitation_order.cpp](../examples/tiebreak_visitation_order.cpp):
two `b`-prefixed producers go by which is *visited* first, not alphabetically),
and *especially* when they share a full name — two `rfactor` intermediates both
printed `g_intm`, so prefix and name tie and first-visitation is the **only**
deciding field. This same ranking orders sibling producers filed at any single
`compute_at` level, not just root (§7).

#### First-visitation index

First-visitation order is a *pre-order* depth-first walk from the output(s),
separate from the realization walk: on reaching a Func, **stamp it with the next
index the first time it is seen**, then descend into the Funcs it calls, skipping
any already stamped. "The Funcs it calls, in order" means the calls across the
Func's whole definition, in this order:

- **Stages first-to-last** — the pure (init) definition, then each update stage in
  order (§3), so a producer read only in a later update is stamped later.
- **Within a stage, in the compiler's definition order**: first the RDom
  **predicate** reads (a reduction's `where`-clause, if any — rarely the deciding
  read), then the **RHS** value reads (what the
  stage computes), then the **LHS** index reads (where it stores). A Func read
  **only on the LHS** — a data-dependent scatter index, e.g. `hist(idx(x)) += 1` —
  is still visited and still gets an index; it is just stamped *after* that
  stage's predicate and RHS reads. (It is a genuine producer: `idx` must be
  computed before the stage can run, so it needs a slot like any other.)
- **Then the stage's `specialize` branches**, in declaration order, recursively
  (§15) — so a producer read only in a branch is stamped after the base
  definition's reads of the *same* stage.

(This mirrors the compiler's own definition walk — predicate, then values, then
args, then specializations — in `DefinitionContents::accept`.)

#### Fused groups: one contracted vertex (forward reference: `compute_with`, §14)

Because the tie-break lives on the **edge label**, `compute_with` (§14) is a plain
graph operation: **contract the group's members into a single vertex**. Contraction
in a *multigraph* keeps every edge — it never merges or relabels them — so the
group vertex has:

- **out-edges** = the union of the members' out-edges (to the members' producers),
  labels intact — so the group is realized once, after everything *any* member
  reads;
- **in-edges** = the union of the members' in-edges (from consumers), **each still
  carrying the label of the member it originally pointed at**.

Those preserved in-edge labels are the whole subtlety, and they are ordinary
multigraph edges — not a "half-collapse": a consumer that read member `a` has an
edge into the group labelled with `a`'s key, a consumer that read member `z` has
one labelled with `z`'s key. So the group vertex has **no key of its own** — where
it sorts among a given consumer's other producers depends on *which member that
consumer read*, i.e. on the edge label. Two consequences, both ordinary
labelled-graph facts:

- the group is one vertex, so it precedes every consumer of any member and follows
  every producer of any member
  ([examples/fused_group_consumer_interleave.cpp](../examples/fused_group_consumer_interleave.cpp));
- flipping which member a consumer reads relabels that consumer's edge into the
  group, moving the group relative to the consumer's other producers
  ([examples/fused_group_edge_keyed_tiebreak.cpp](../examples/fused_group_edge_keyed_tiebreak.cpp)).

§14 covers the group's internal structure; for realization order it is exactly
this one contracted vertex, edges and labels preserved.
