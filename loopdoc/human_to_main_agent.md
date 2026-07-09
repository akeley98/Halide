# Human-added Tasks

Human: I asked a question (honestly a leading question) to the micro agent elicit some criticism of the current "realization order in detail" section.
Honestly, I think the structure was set when the understanding of realization order we had was inaccurate,
and more things have been bolted-on to compensate for misunderstandings instead of restructuring).

Since you've identified some failing cases related to realization order and fused groups, this is a golden opportunity to improve loopdoc.md and get at least some micro-agent testing of it.

The goals are

* Make the "realization order in detail" more to the point and not a large number of words that don't quickly describe the actions taken to create this realization order.
* At least consider the commentary in `micro_agent_on_realization_order.md` when re-structuring
* Make sure `compute_with` and realization order are documented together holistically.
* Corollary, move information about realization order X fused groups out of the last "putting it all together" section.
  The conclusion shouldn't be the first time this is brought up, and there's not enough word budget here anyway to explain this complicated interaction.

When restructuring, please consider being explicit about the "nodes" and "edges" in the graph that is being topological sorted.
It seems that we can imagine each "edge" is annotated with some stuff (prefix, name, etc.?) that influences how ties are broken in DFS visitation order.
Will also have to make a forward reference to the not-really-explained-yet `compute_with` feature.
But you can say that this future feature combines a bunch of functions in a fused group (which will be defined later) into a single node whose outgoing edges are the union of ...

Of course the `compute_with` section should briefly call back to describe how fused groups = node for realization order (But much more briefly).
