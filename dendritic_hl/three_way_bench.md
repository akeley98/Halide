This is the best I can come up with for now that's implementable within internship bounds.

# Generator Parameters

First off, I have to tackle the discarded generator parameters hole.
I'm somewhat regretting adding this feature, because it's maybe turned
out to be more of a headache than can be justified for a prototype.
The main reason was to automate tedious parameter sweeps and not churn
LLM time on that; my GPU experience told me that matters but ehhh...

Anyway, if I were to keep the feature, probably I should just specify a
`generator_parameters.json` to be part of a schedule node's state,
rather than just have it provided from "somewhere" as part of the
naive `dh_hl profile` tool's input.

Then just hard wire it that benchmarking a schedule node automatically
sweeps over all the stored parameters (maybe add a fast path "use
default generator parameters only" for brief experiments, but this is
not used for official rankings). If the user/agent is dissatisfied
with the parameters, they will have to accept a new schedule node
(possibly with an added "improve generator parameters" idea node, if
they are disatisfied with a canonical schedule).

Will have to hash schedule nodes based on `generator.cpp` and
`generator_parameters.json` together now.

# Anchor Schedule

The main agent has to pick an anchor schedule, which is inherited by
sub-agents. This is tricky at the very beginning, and should not be
mandatory in the early phases, when you are just seeking *very
obvious* improvements.

At the start of the session, I provided no schedule, which churns
forever in benchmarking. So that cannot be the anchor.
Fortunately, the default of "accept the terminus schedule,
with a warning [no anchor schedule chosen] if there isn't one"
will cover this case.

# Benchmark Sets: Baked-in Triples

The "comparison campaign" will be specialized now to benchmark triples of schedule nodes:

1. The target schedule node ["unambiguous schedule node", for now].

2. The session's anchor schedule node, if it exists.

3. A third schedule, by default the target's parent idea's parent
   schedule node, if it exists.

The (1, 2) pair can be used for ranking the current ideas set.

The (1, 3) pair can be used for obsoleted-by checks. Even if the
agent isn't thinking *right now* about obsoleted-by, the fact the
benchmark tool will quietly pull the parent into the comparison
without asking means the data for this will appear organically.

Nevertheless, (3) is overridable in case the agent is curious about a
specific pairing, regardless of the tree structure.

### Global Cost Ordering

For each schedule `S` that needs to be ranked, search for benchmark
sets that compare `S` to the anchor `A`. If no such benchmarks are
found, `S` is cost-modelled with the "not yet benchmarked" sentinel.
Otherwise, for all batches in all benchmark sets, extract one sample
value `min_runtime(S)/min_runtime(A)`, and make the cost the median of
all such values.

If there's no anchor `A` at all, fall back to the median for all
benchmarks of `S` found (units go from dimensionless to time, but this
is OK, because "no anchor at all" is global). Issue a warning, with a
brief explanation of possible noise consequences. We DO need this
fallback to work though, because of the bootstrapping problem.

Claude's warning: "ranking is drift-exposed until you set an anchor"

TODO: consider warning if `A` drifts far from `S` performance.
Large gaps make `A` less effective as an anti-drift mechanism.

### Obsoleted-by

The obsolete check run for parent/child (`P`/`C`) is basically as you
proposed. Search for all benchmark sets containing a comparison
between `P` and `C`, aggregate all their batches, and extract
one sample value `min_runtime(C) / min_runtime(P)`.
If the confidence interval of all these values is strictly less than 0,
then conclude `P` is obsoleted-by the faster `C`.

If no such benchmark sets are found, just quietly not report anything.
This is the degenerate case of 0 samples found (<=2 implies NaN
confidence interval). Obsoleted-by is just a convenience to help the
agent drop ideas buried in the interior of the sub-tree.

Claude argued for divide instead of subtract: obsoleted-by uses a
difference; a ratio would be more powerful (minor-medium).
`min(C) − min(P)` is sign-robust (so the CI < 0 conclusion is safe),
but under multiplicative drift each same-batch difference is scaled by that
batch's factor m(b)·(τC − τP), so the magnitude jitters
batch-to-batch, widening the CI and costing you significance
power. Using `min(C)/min(P)` < 1 (or the log-difference) cancels m(b) in
magnitude too → tighter CI, and it's consistent with how you compute
cost. Same sign conclusion, more sensitivity.

### Siblings

As it stands, I do see your objection about sibling ideas.
I was hoping I could use the superseded-by links cleverly,
but couldn't figure out a good way to do it.
So it's left to the agent to use an explicit comparison
benchmark if they want to compare siblings.

To some extent, the "better sibling nodes" issue is only a very
specific case of the should-we-focus-on-a-better-alternative-in-another-subtree
issue inherent in a search that values variety, which is left to agent judgment.

### Warnings

Discard benchmark sets with the wrong `profiler_version` with warning.

Warning if benchmarks have mixed hostname or cpu counts.

