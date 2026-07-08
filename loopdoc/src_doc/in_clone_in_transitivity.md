# in() / clone_in(): the transitivity of "which Funcs are affected"

_Part of the [src_doc set](README.md); a §13 companion to
[in_clone_in.md](in_clone_in.md). Cross-file references are written as "§N"._

## 13 (companion). What "acts transitively" actually resolves to

`Func::in` / `Func::clone_in` are documented in `src/Func.h` with a claim that
reads simply enough:

> As with `Func::in()`, `clone_in()` acts transitively: any Func in `'f'`/`'fs'`
> that does not directly call this Func is replaced by the set of direct callers
> reachable from it along paths that lead to this Func. Only this Func is cloned;
> the intermediate Funcs along the path are not. … If the anonymous reduction
> Func had other consumers besides `g`, they would also see the rewrite from `f`
> to `f_clone` — only this Func is cloned, not the intermediates.

Every clause of that is true, but it hides three things that decide *which*
Funcs a wrapper/clone actually affects: (a) the walk **stops at the first direct
caller** on each branch, (b) it runs against the **current, un-wrapped** call
graph at `in()`/`clone_in()` *call* time, and (c) because the wrapper is pinned
onto a shared *intermediate*, it redirects that intermediate **for every one of
its consumers**, not only the one you named. The last line of the header comment
gestures at (c) in passing; this note makes all three precise, with source. It
also explains the `c3` situation (probe `../probe/probe_clone_c3_sharing.cpp`;
the wrapper analogue is the committed example
[../examples/in_but_inlined.hpp](../examples/in_but_inlined.hpp)) and the
double-clone crash (probe `../probe/probe_clone_double_crash.cpp`).

### The resolution algorithm (call time)

All `in`/`clone_in` forms funnel through `get_wrapper` (`src/Func.cpp` ~2242),
which normalizes the user-supplied consumer list **before** recording anything:

```
vector<Func> fs = fs_in.empty() ? fs_in
                                 : resolve_transitive_callers(wrapped, fs_in);
```

`resolve_transitive_callers` (`src/Func.cpp` ~2219) maps each named consumer to a
set of Funcs via `collect_direct_callers_of` (~2193):

```
collect_direct_callers_of(target, start, visited, result):
    if start == target: return                       # don't wrap the Func in itself
    if start already visited: return
    direct = find_direct_calls(start)                # start's DIRECT callees, current IR
    if target ∈ direct:
        result += start                              # start directly calls target...
        return                                       # ...STOP — do not descend past it
    for callee in direct:
        collect_direct_callers_of(target, callee, ...)
```

So from a named consumer the walk descends the call graph and, on each branch,
records the **first** Func that *directly* calls the wrapped Func, then stops
that branch (the comment at ~2190: "stop descending that branch — we don't want
to pick up unrelated direct callers that happen to live deeper in the subtree").
Three consequences:

* **A consumer that reaches the wrapped Func only through intermediates is
  fine** — the wrapper is pinned onto the intermediate that directly calls it,
  not onto the named consumer. `common.clone_in(c1)` with
  `c1 → maybe_inlined → common` resolves to `{maybe_inlined}`.
* **A consumer that itself directly calls the wrapped Func resolves to itself and
  the walk stops immediately** — even if that same consumer *also* reaches the
  Func through an intermediate, the indirect path is **not** separately pinned by
  this call. `c3(x,y) = maybe_inlined(x,y) + common(x,y) + …` resolves
  `common.clone_in(c3)` to `{c3}` (because `c3` directly calls `common`); the
  `c3 → maybe_inlined → common` path is left for whoever pins `maybe_inlined`.
* **A consumer with no static path to the wrapped Func is left as-is**
  (`resolve_transitive_callers` ~2233: if `direct_callers.empty()`, emit the
  original Func). The wrapper is then registered under that consumer's own name
  and, unless something else makes it call the Func, simply never triggers —
  or, at lowering, fails the "does not call" check (§13 legality; see below).

`find_direct_calls` reads the Func's **current in-memory definition**. Wrapper
rewrites are *not* applied to those definitions at `in()` time — they are
deferred to a lowering pass (`wrap_func_calls`, next subsection). So the graph
the walk sees is always the **original** call structure, regardless of earlier
`in`/`clone_in` calls in the same program. This matters for chained wrappers
(below).

The resolved set is what `add_wrapper` records, keyed by name, **on the wrapped
Func** (`src/Function.cpp` ~1229): `wrapped.func_schedule.wrappers()[caller] =
wrapper`. The named consumers' own `FunctionContents` are never touched at call
time (see [in_clone_in.md](in_clone_in.md) part (b)).

### Reuse and validation: overlapping pin-sets make wrap order matter

`get_wrapper` (`src/Func.cpp` ~2242) does not always build a new wrapper. It keys
the decision on the resolved set's **first** element `fs[0]`:

```
iter = wrappers.find(fs[0].name())
if not found:                        # build a NEW wrapper
    for i in 1..fs.size():           #   but first assert the other pins are unwrapped
        user_assert(wrappers.count(fs[i]) == 0)
            "... already has a wrapper while fs[0] doesn't"
    create wrapper; add_wrapper(f, wrapper) for every f in fs
else:                                # REUSE the existing wrapper for fs[0]
    validate_wrapper(wrappers, fs, iter->second)   # exact-match check
    return iter->second              #   (no add_wrapper — extra pins are NOT registered)
```

`validate_wrapper` (~2143) requires the reused wrapper's recorded consumers to
match `fs` exactly: every existing entry in `fs` must map to this wrapper, and
every existing entry **not** in `fs` must not. Consequences (probe
`../probe/probe_in_key_set_collision.cpp`), for two calls whose pin-sets overlap
but differ, e.g. `{common2, mid}` and `{common2}`:

- **superset first, then subset**: the subset call reuses via `fs[0]=common2`, but
  `mid` shares the wrapper and is not in the subset → `validate_wrapper` rejects
  ("… `mid` shares the same wrapper but is not part of the redefinition").
- **subset first, then superset**: the superset call reuses via `fs[0]=common2`;
  the only recorded entry is `common2` (`==fs[0]`, skipped) → passes, returns the
  wrapper, and **never registers the extra key** `mid` — so `mid` silently keeps
  reading the original.
- which reject path fires can even flip on the **alphabetical** order of the pins
  (whichever sorts first becomes `fs[0]`): if the non-shared pin sorts first, the
  reuse `find(fs[0])` misses and the *create-branch* assert ("already has a
  wrapper while … doesn't") fires instead.

So wrap order is order-free only when the two calls' pin-sets are **equal**
(idempotent reuse; only the generated name follows the first call) or **disjoint**
(two independent wrappers — `../probe/probe_in_two_wrappers_levels.cpp` schedules
two disjoint-pin wrappers at different loop levels). **Overlapping-but-unequal**
pin-sets are order-sensitive. (This corrects an earlier over-broad "wrap order
never matters" claim, which the collision probe falsifies.)

### The rewrite is on the intermediate, so it is shared

Deferred application happens in `wrap_func_calls` (`src/WrapCalls.cpp`), which
`print_loop_nest` runs at lower time. For a custom wrapper it registers the
substitution `{wrapped → wrapper}` **for the pinned Func only**, then calls
`Function::substitute_calls` on that Func's body (`src/WrapCalls.cpp` ~154).
Crucially the substitution rewrites the pinned Func's *definition*, which is a
single shared object: **every** consumer that reads the pinned intermediate now
reads the version whose body calls the wrapper. The wrapper does not know or care
which consumer "asked" for it.

This is the crux the header comment states only in passing. Worked example
(reproduced in `../probe/probe_clone_c3_sharing.cpp`; the committed wrapper
analogue is [../examples/in_but_inlined.hpp](../examples/in_but_inlined.hpp)):

```
common(x,y)        = in(x,y)
maybe_inlined(x,y) = common(...) + common(...)
c1(x,y)            = maybe_inlined(...) + maybe_inlined(...)
c3(x,y)            = maybe_inlined(x,y) + common(x,y) + common(x+1,y+1)
common.clone_in(c1);      // resolves to {maybe_inlined}
```

`common.clone_in(c1)` pins the clone on `maybe_inlined`. At lowering
`maybe_inlined`'s body is rewritten to read `common_clone_in_c1`. Because `c3`
*also* reads `maybe_inlined`, `c3` sees the clone **through that path** — even
though `c3` was never named. But `c3`'s own **direct** reads of `common` are
untouched, so `c3` reads **both** `common_clone_in_c1` (via `maybe_inlined`) and
the original `common` (directly). The printed nest confirms it: both
`produce common_clone_in_c1` and `produce common` appear, and `common` survives
precisely because `c3`'s direct reads keep it alive. This is the "weird `c3`"
the example flags — it is not weird once you see that the pin is on the shared
`maybe_inlined`, not on a per-consumer edge.

### Partial routing: "clone for `g`" ≠ "everything `g` computes uses the clone"

The most counter-intuitive consequence is that naming a consumer does **not**
route all of that consumer's transitive uses of the wrapped Func through the
clone — only the reads on the **first-direct-caller frontier** are redirected,
and any use reached *past* that frontier keeps the original. `print_loop_nest`
cannot show this (the RHS is elided); the reproducer
`../probe/probe_clone_partial_routing.cpp` dumps `compile_to_lowered_stmt` so the
actual loads are visible. The DAG:

```
maybe_inline → clone_me → c1 → c2 → out
                      \-------→ c2      (c2 ALSO reads clone_me directly)
```

with `clone_me`, the clone, and `c2` all `compute_root` and `c1` computed at
`c2`. `clone_me.clone_in(target)` for the three reachable targets gives:

| `target` | wrapper pinned on | reads the **clone** | reads the **original `clone_me`** |
|---|---|---|---|
| `c1` | `c1` | `c1` (so `c2` via `c1`) | `c2`'s *direct* reads |
| `c2` | `c2` | `c2`'s *direct* reads | `c1` (so `c2` via `c1`) |
| `out` | **`c2`** | `c2`'s *direct* reads | `c1` |

Two things fall out, both verified in the lowered stmts:

* **The named consumer is often not the Func that is modified, and its uses are
  split.** `clone_in(out)` is *identical* to `clone_in(c2)`: the walk from `out`
  finds `out` does not call `clone_me` directly, descends to `c2`, stops at the
  first direct caller, and pins there. The clone is *named* `clone_me_clone_in_out`
  yet `out` itself is untouched, and `out`'s dependence on `clone_me` is served by
  a **mix** — the clone through `c2`'s direct reads, the original through
  `c2 → c1`. So "clone for `out`" did not make all of `out`'s `clone_me` reads go
  through the clone.
* **Shadowing.** In the `c2` case, `c2`'s own direct read of `clone_me` stops the
  walk at `c2`, so the deeper `c1` never gets the clone — even though `c1` is on a
  path from `c2` to `clone_me`. Remove `c2`'s direct read and the walk would
  descend to `c1` and redirect it instead. A closer direct caller **shadows** the
  ones behind it.

(And the shared-body bleed-through from the previous subsection still applies:
in the `c1` case the clone was requested for `c1`, but `c2` reads `c1`, so `c2`
picks up the clone through that edge too — unrequested — on top of its own direct
reads of the original.)

The mental correction: **`f.in(g)` / `f.clone_in(g)` means "redirect the first
Funcs that directly call `f` on the paths down from `g`," not "make `g` and
everything under it use the wrapper/clone."** Partial routing, the named-consumer
not being touched, shadowing, and unnamed-consumer bleed-through are all the same
fact seen from different angles.

### Blind to earlier wrappers: the walk uses the pre-wrap graph

`collect_direct_callers_of` calls `find_direct_calls` on the in-memory
definitions, and wrapper rewrites are applied lazily at lowering (not at call
time). So the walk plans against the call graph *before* any earlier
`in`/`clone_in` redirection — it cannot see that a consumer will, post-wrap, read
a different Func. The pin can therefore land on a caller that the named consumer
no longer routes through, stranding the clone on some *other* consumer.

Worked case ([../examples/indirectly_reached_clone.hpp](../examples/indirectly_reached_clone.hpp);
lowered-graph reproducer would mirror `probe_clone_partial_routing.cpp`). Edges
consumer → producer: `f → common`, `g → f`, `h → f`, `out → g, h`.

```
f.clone_in(g)          => post-wrap: g → f_clone_in_g,  h → f
common.clone_in(g):
   walk from g (PRE-wrap): g → f → common  => pin on f
   lowering: f's read of common → common_clone_in_g
             f_clone_in_g is a copy of f's body → reads the ORIGINAL common
   net: h (via f) reads common_clone_in_g;  g (via f_clone_in_g) reads common
```

- The clone requested for `g` is consumed by `h`, not `g` — inverted.
  `common_clone_in_g.compute_at(g, y)` is illegal; `compute_at(h, y)` is legal.
- Order-independent (both the `f`-copy and the `common`-rewrite are lazy).
- `common.clone_in(h)` is identical (both paths go through `f` pre-wrap → pin
  always `f` → `f` feeds only `h`).
- micro_halide matches Halide on all combinations of the example, but this is not
  derivable from "which consumers are redirected" without the pre-wrap-graph fact.

### Legality re-check at lowering (why a resolved pin can still fail)

`validate_custom_wrapper` (`src/WrapCalls.cpp` ~53) runs **after** substitution
and asserts the pinned Func now directly calls the wrapper — i.e. that the
substitution actually landed. It fails with `Cannot wrap "f" in "g" because "g"
does not call "f"` when the pin is on a Func that does not (any longer) call the
wrapped Func. Two ways that happens, both detailed in loopdoc §13 / this src
file: no path at all at call time (so the consumer itself was pinned); or a
later directive severed the pinned Func's call — e.g. `rfactor` moving a read
into a fresh intermediate — which is a **stale pin**, because the resolution
above is computed once at call time and never recomputed.

### The double-clone crash (cloning an already-wrapped Func)

The example's commented-out `common.clone_in(c3)` (after `common.clone_in(c1)`)
"crashes Halide." It is an `internal_assert`, not a user error, and it fires at
`clone_in` **call** time, not at lowering. Reproduced message:

```
Internal error at src/Schedule.cpp:372
Condition failed: copied_func.defined()
common_clone_in_c1$0$0
```

Cause, from source: `create_clone_wrapper` (`src/Func.cpp` ~2176) builds the
clone by `deep_copy`-ing the wrapped Func's own `FunctionContents`, seeding the
deep-copy `copied_map` with only the wrapped Func's self-remapping. That deep
copy includes the wrapped Func's `func_schedule`, and `FuncSchedule::deep_copy`
(`src/Schedule.cpp` ~369-373) tries to **carry over the wrapped Func's existing
`wrappers` map**, remapping each entry through `copied_map`:

```
for (const auto &iter : contents->wrappers) {
    FunctionPtr &copied_func = copied_map[iter.second];
    internal_assert(copied_func.defined()) << Function(iter.second).name();   // :372
    copy.contents->wrappers[iter.first] = copied_func;
}
```

When `common` already has a wrapper (`common_clone_in_c1` from the first clone),
that entry is present in `common`'s schedule, but the single-Func clone path
never put `common_clone_in_c1` into `copied_map` — so the lookup yields an
undefined `FunctionPtr` and the assert trips (the doubled `$0$0` suffix is
`deep_copy` re-suffixing the already-suffixed wrapper name).

**This is general, not specific to the `c3` "paradox" — but it is gated on
`create_clone_wrapper` actually *running*.** The crash trigger is only that
`create_clone_wrapper` deep-copies the wrapped Func's schedule while that
schedule already holds *some* wrapper; it does not depend on the consumer
arguments or on any overlap. The subtlety is *when a `clone_in` call reaches
`create_clone_wrapper` at all*: `get_wrapper` first resolves the consumer(s) to
their key(s) and, **if a wrapper for that key already exists, returns the cached
one without building anything** (`src/Func.cpp` ~2288, the `iter != end` branch).
So a repeat `clone_in` for an *already-registered* consumer set is idempotent and
safe; only a `clone_in` that must create a wrapper for a **new** consumer key
hits the deep copy. Verified (`../probe/probe_clone_double_crash.cpp` for the
`c3` shape; `../probe/probe_clone_combos.cpp` and `../probe/probe_clone_idempotent.cpp`
for the rest), sequences on one wrapped Func `f` with consumers `a`, `b`:

| sequence | result | why |
|---|---|---|
| `f.clone_in(a); f.clone_in(a);` | OK | 2nd resolves to the existing key `a` → cached wrapper returned, no deep copy |
| `f.clone_in(a); f.clone_in(b);` | **crash** | 2nd is a new key → `create_clone_wrapper` deep-copies `f`'s now-wrapper-bearing schedule |
| `f.in(a); f.clone_in(b);` | **crash** | same — the pre-existing wrapper came from `in()`, still in `f`'s schedule |
| `f.clone_in(a); f.in(b);` | OK | `in()` (`create_in_wrapper`) builds a fresh pointwise Func; never deep-copies |
| `f.in(a); f.in(b);` | OK | neither call deep-copies |

So the rule is: a Func may carry **at most one distinct `clone_in`/`in`-created
wrapper without breaking a later clone**. Re-requesting an already-registered
consumer set is fine (idempotent — this is what Halide's own
`test/correctness/func_clone.cpp:43-44` exercises, with reordered-but-equal
consumer lists). What is unsupported is a `clone_in` that must build a **second,
distinct** wrapper on a Func that already has one: it aborts internally. `in()`
has no such restriction because it never deep-copies the wrapped Func. The
single-Func clone path simply does not cooperate with the whole-pipeline
`deep_copy` free function (which pre-seeds `copied_map` with *every* Func,
[in_clone_in.md](in_clone_in.md) verdict) that would make the wrapper remap
resolvable. This is a Halide limitation, not a schedule the user got wrong; the
example rightly sets it aside. Note the distinct, non-crashing case that *is*
in Halide's suite: cloning a **clone result** (`a.clone_in({b,e})` then
`.clone_in(e)` on the *returned* Func, `func_clone.cpp:254-256`) is fine, because
the clone wrapper starts with an empty `wrappers` map — it is the *original*
already-wrapped Func that cannot be re-cloned.

**Known upstream, still open, unfixed** (web search, 2026-07-08). This is not a
fork artifact — it is tracked by two open `halide/Halide` issues, both quoting
the same `copied_func.defined()` assert in `FuncSchedule::deep_copy` (the line
number drifts across versions, `Schedule.cpp:336`→`:348`→`:372`):
[#6476 "Cannot `clone_in()` two different functions"](https://github.com/halide/Halide/issues/6476)
(the exact two-distinct-consumers case) and
[#3661 "Applying `clone_in` to a same function twice causes an internal error"](https://github.com/halide/Halide/issues/3661).
The #6476 thread reaches the same root cause (the first `clone_in` mutates the
wrapped Func's schedule, so the second's `deep_copy` cannot remap the pre-existing
wrapper). Neither issue has a linked fix; the nearest merged work,
[PR #9117](https://github.com/halide/Halide/pull/9117) (makes `in`/`clone_in`
transitive — see the resolution walk above), does **not** touch the `copied_map`
seeding and does not close either issue. So the fix (seed `copied_map` with the
wrapped Func's existing `wrappers` entries) would be a genuine upstream
contribution.
(It is out of scope for micro_halide, which does not model group-level deep copy;
the relevant micro behavior is the call-time resolution and the shared-
intermediate rewrite above.)

### Summary for micro_halide

To mirror Halide's transitivity faithfully a re-implementation must, at
`in`/`clone_in` call time: (1) for each named consumer, walk the **current**
(un-wrapped) call graph and collect the **first direct caller** of the wrapped
Func on each branch, stopping there; (2) leave a consumer with no path untouched;
(3) record `{pinned_caller_name → wrapper}` on the **wrapped** Func; and, at nest
build time, (4) rewrite each pinned Func's reads of the wrapped Func to the
wrapper — which automatically makes the redirection visible to **all** consumers
of that pinned Func, and only to the wrapped Func's direct reads within it. The
per-consumer illusion in the API surface is exactly that: the state is
per-*intermediate*, and sharing falls out of rewriting the intermediate.
