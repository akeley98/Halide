# `in` and `clone_in`: transitivity and surprises

Detail companion to the main [loopdoc.md](../loopdoc.md); section references "§N" point to that document.

The full pin-resolution rules for `in`/`clone_in` and the surprises that follow when a named consumer does not directly call the wrapped Func. See the main doc's §13 for the recommendation that avoids all of this.

---

Both directives create a **new, separate Func** that a chosen set of consumers
read *instead of* the original. They differ in what that new Func computes and in
the schedule it starts with, and you schedule it like any other. A **wrapper**
(`in`) starts with the usual **default inline** schedule (§4–§5); a **clone**
(`clone_in`) starts from a *copy of `f`'s current schedule* (see below), which is
the default only if `f` is still unscheduled when you clone. While the new Func is
inline it is *non-realized* — a pure wrapper or clone is substituted away like any
other inline pure Func (§5), a *non-pure* one follows the non-pure inline default
(§11) — so it has **no visible effect on the nest until it has a compute level**
(its own, or one inherited from `f`). The example nests below assume the new Func
is realized (e.g. `compute_root`).

### `f.in(g)` — an identity *wrapper*

`f.in(g)` returns a new Func (printed `f_in_g`) whose definition is the pointwise
identity `f_in_g(args) = f(args)`; `g` reads `f_in_g` where it used to read `f`,
and `f`'s other consumers are untouched. The wrapper is pure, so at its default
it inlines straight back (the nest is just `f` → `g`); give it a compute level to
make it a distinct node (realization order `f` → `f_in_g` → `g`):

```
produce f:
  ...
consume f:
  produce f_in_g:        # the wrapper reads f
    for ...: f_in_g(...) = ...
  consume f_in_g:
    produce g:           # g now reads f_in_g, not f
      ...
```

Forms: `f.in(g)`, `f.in({g1, g2, …})` (one shared wrapper for several named
consumers), and `f.in()` (a single **global** wrapper used by every consumer that
has no custom wrapper of its own). A global wrapper coexists with custom ones: a
consumer with its own custom wrapper uses that (**custom takes precedence**),
everyone else uses the global one — **except `f`'s own wrappers, which always
read `f`** ([in_custom_and_global.cpp](../examples/in_custom_and_global.cpp): `g1`'s
custom wrapper and the global wrapper are siblings, both reading `f`). Common
uses: a per-consumer staging point for a shared producer, and repairing the "two
consumers force `f` to `root`" situation (§7,
[neg_compute_at_two_consumers.cpp](../examples/neg_compute_at_two_consumers.cpp)) by
wrapping `f` separately per consumer.

### `f.clone_in(g)` — an independent *clone*

`f.clone_in(g)` returns a new Func that is a **copy of `f`'s entire definition**
(all stages, schedule, and specializations) *as they stand when you call
`clone_in`* — so a default schedule only if `f` is still unscheduled — and makes
`g` read the clone. Unlike
a wrapper, the clone *recomputes* `f`'s work rather than reading `f`'s result, so
`f` and the clone are independent and may be scheduled differently.

Each wrapper/clone is a **distinct** Func with its own auto-generated name
(`f_in_g`, `f_clone_in_g`, plus an internal `$n` suffix the printer strips) — a
separate node in the nest (§10 normalizes names to positional ids), never "the
same Func twice."

### Two phases: eager at call time, deferred at lowering

Almost every surprise in this section comes from `in`/`clone_in` doing their work
in two phases:

- **Call time (eager)** — the moment you call `f.in(g)` / `f.clone_in(g)`:
  * the new Func is built. An `in` wrapper is a fresh identity `wrapper(args) =
    f(args)`. A **clone deep-copies `f`'s *current* contents** — definition,
    schedule, specializations — so it **freezes** whatever `f`'s body reads right
    now (its callees are *shared*, not copied — see the shared-inputs surprise).
  * the **recursive search** (next subsection) picks which Funcs to redirect and
    records `{pinned Func → new Func}` on `f`. **This pin set is frozen here and
    never recomputed.**
- **Lowering (deferred)** — when the nest is built:
  * each pinned Func's calls to `f` are rewritten to the new Func (the only point
    a consumer's body changes);
  * each pin is re-checked — the pinned Func must *still* call `f`, else the
    schedule is rejected.

The consumer Funcs are untouched at call time; only the record on `f` changes
(see the Implementation note). Two ordering facts follow, and both have sharp
exceptions covered in the surprises below:

- A deferred rewrite does not change the call **graph**, so it does not change the
  pin-*set* a later call's search computes. It *does* change `f`'s recorded
  **wrapper map**, and a later call **reuses/validates against that map** (keyed
  on its first pin, `get_wrapper` in `src/Func.cpp`). So wrap-vs-wrap order is
  free only when the two calls' resolved pin-sets are **equal or disjoint**;
  when they **overlap but are unequal**, order is observable — it can reject or
  silently under-wrap (colliding-pin-sets surprise).
- An **eager** rewrite of a definition (notably `rfactor`, §12) *does* change the
  call graph, so its order relative to a wrap changes the pin-set itself
  (stale-pin surprise).

### Which Funcs are pinned: the recursive search

The named consumers are not necessarily the Funcs that get redirected. For each
named consumer, Halide walks **down the current call graph** toward `f` and pins
the **first Func that directly calls `f`** on each branch:

```
pin_targets(f, consumer):
    descend `consumer`'s direct calls in the CURRENT graph:
        if this Func directly calls f  ->  pin it; stop descending this branch
        else                            ->  recurse into each direct callee
    if no Func on any branch calls f    ->  pin `consumer` itself
                                            (typically fails the lowering re-check)
global f.in()  ->  every direct caller of f, minus f's own wrappers and any
                   consumer that already has its own custom wrapper
```

Two properties of this walk drive the surprises below:

- It reads the graph **as written now** and is **blind to wrappers/clones** — the
  deferred rewrites are invisible to it, so it plans against *pre-rewrite* edges.
- A pin lands on a **shared** Func; rewriting that one body redirects it for
  **every** consumer of it, not only the consumer you named.

([src_doc: in/clone_in transitivity](../src_doc/in_clone_in_transitivity.md) traces
the search and its lowering-time application in source.)

### Surprise: the named consumer is usually not the Func modified

The search pins the *first direct caller* of `f`, so naming `g` redirects
whatever sits on the frontier below `g`, not `g` itself — and because that Func
is shared, its *other* consumers get redirected too. In
[in_but_inlined.hpp](../examples/in_but_inlined.hpp), `common.in(c1)` where `c1`
reads `common` only through `maybe_inlined` pins the wrapper on **`maybe_inlined`**;
a sibling `c3` that also reads `maybe_inlined` then reads the wrapper as well
(unrequested), while `c3`'s own *direct* reads of `common` stay on the original.
So "wrap for `g`" means "redirect the first direct callers of `f` beneath `g`,"
not "make `g` and everything under it use the wrapper." (A derived Func such as an
`rfactor` intermediate can be a pin target like any other; the full
partial-routing table is in the [src_doc](../src_doc/in_clone_in_transitivity.md).)

### Surprise: pins freeze at call time — `rfactor` order matters (wrap order doesn't)

The lowering re-check rejects a pin whose Func no longer calls `f`:

> `Cannot wrap "f" in "g" because "g" does not call "f"`

Two ways to hit it:

- **No path to `f` at all** — the search falls back to pinning the named consumer
  itself, which never calls `f` (`out.clone_in(g)` when `g` reads `f` but not
  `out` — [clone_in_unused.cpp](../examples/clone_in_unused.cpp), a negative example).
- **A later eager rewrite severs the pinned call.** The pin is frozen at call
  time; `rfactor` (§12) then rewrites the definition, moving the read of `f` into
  a new intermediate, so the pin goes *stale*:

```
rfactor(h) THEN clone_in({g,h}): search sees h → h_intm → f, pins h_intm   (legal; naming h_intm is redundant)
clone_in({g,h}) THEN rfactor(h): pins h (h→f then); rfactor makes h→h_intm  (stale pin → the error above)
```

`rfactor` is an eager rewrite of the graph the search reads, so its order versus
a wrap changes the pin-set. Fix: `rfactor` first, then wrap.
(`clone_specialize_matrix_impl.hpp` choiceB=2 is the stale negative, choiceB=3 the
working order — the stale pin is not a claim `h` can't reach `f`; it still does,
via `h_intm`, but the pin was taken on `h`.)

### Surprise: colliding pin-sets — wrap order *can* matter

Two `in`/`clone_in` calls whose resolved pin-sets **overlap but are unequal** are
order-dependent, even though both are lazy wraps and neither changes the call
graph. `get_wrapper` decides reuse from the call's **first** pin (`fs[0]`) and
`validate_wrapper` (`src/Func.cpp`) demands the reused wrapper's recorded consumer
set match *exactly*. With `a → common1`, `b → common1`, and `out1 → {common2, mid}`,
`out2 → {common2}` both funnelling to `common1` (pin-sets `{common2, mid}` and
`{common2}`, overlapping on `common2`):

```
in(out1) then in(out2): out1 registers {common2→w, mid→w}; out2 reuses via common2,
                        but mid shares w and is not in out2's set  -> CompileError
in(out2) then in(out1): out2 registers {common2→w}; out1 reuses via common2 and
                        SILENTLY drops the extra key mid           -> mid keeps reading the original
```

Which failure you get even depends on which pin sorts first alphabetically (it
becomes `fs[0]`): a different sort routes the same collision through
`get_wrapper`'s other reject path ("… already has a wrapper while … doesn't").
So *equal* pin-sets are order-free (the second call is an idempotent reuse — only
the wrapper's generated name follows the first call) and *disjoint* pin-sets are
order-free (two independent wrappers,
[probe/probe_in_two_wrappers_levels.cpp](../probe/probe_in_two_wrappers_levels.cpp)
schedules them at different levels), but *overlapping-unequal* sets are not.
Two consumers funnelling into `f` through a shared intermediate therefore cannot
be given separate wrappers. (Source walk + both orders:
[probe/probe_in_key_set_collision.cpp](../probe/probe_in_key_set_collision.cpp).)

### Surprise: the search is blind to pending rewrites — a clone can feed a consumer you didn't name

Because the search ignores earlier wraps' deferred rewrites, it can pin on a Func
the named consumer will not actually read in the final graph. With `f.clone_in(g)`
already recorded (final graph `g → f_clone_in_g`, `h → f`), calling
`common.clone_in(g)` still walks the *pre-wrap* `g → f → common` and pins on `f`:

```
walk from g:  g → f (pre-wrap) → common     ⇒ pin f
lowering:     f's read of common → common_clone_in_g
              f_clone_in_g is a frozen copy of f's body ⇒ still reads the original common
```

so the clone requested "for `g`" is read by **`h`** (the only post-wrap reader of
`f`), while `g` reads the original `common`: `common_clone_in_g.compute_at(h, y)`
is legal, `compute_at(g, y)` is not
([indirectly_reached_clone.hpp](../examples/indirectly_reached_clone.hpp);
order-independent, and `common.clone_in(h)` gives the same result since both `g`
and `h` route through `f`).

### Surprise: a clone shares `f`'s inputs (callees are not copied)

The deep copy duplicates `f` but reads the **same** producers `f` reads. So if
`f` reads `p`, then after `f.clone_in(g)` the Func `p` is read in two places (`f`
and the clone) and the only level enclosing both is `root`: `p.compute_at(f, x)`
becomes **illegal** — Halide lists `p` "used in" both, with only
`p.compute_root()` legal
([neg_clone_shared_callee.cpp](../examples/neg_clone_shared_callee.cpp)). To give a
clone private inputs, clone those too. (`Func::clone_in`'s "intermediate Funcs
along the path" is the *caller* chain between the consumers and `f`, not `f`'s
callees.)

### Surprise: a clone can delete `f`; a wrapper never does

A redirected caller reads the new Func instead of `f`, so `f` keeps only its
non-redirected readers. A wrapper reads `f`, so **`f` always survives an `in`**.
A clone reads `f`'s *inputs*, so if **every** reader of `f` is redirected to the
clone, `f` becomes unreachable (§1) and **drops out of the nest**. For the chain
`h → g → f` with no other reader: `f.in(h)` prints `f → f_in_h → g → h` (`f`
stays); `f.clone_in(h)` prints `f_clone_in_h → g → h` with `f` absent
([in_transitive.cpp](../examples/in_transitive.cpp) vs
[clone_transitive.cpp](../examples/clone_transitive.cpp)).

### Limitation: a Func can be cloned only once

`clone_in` deep-copies `f`'s schedule but not the wrapper entries that schedule
now holds, so a **second, distinct** clone/wrap on an already-wrapped Func aborts
(`copied_func.defined()` in `FuncSchedule::deep_copy`). `f.clone_in(a)` then
`f.clone_in(a)` is fine (returns the first clone); `f.clone_in(a)` then
`f.clone_in(b)`, or `f.in(a)` then `f.clone_in(b)`, crashes. `in()` is exempt (it
never deep-copies). Known, still-open upstream bug
([#6476](https://github.com/halide/Halide/issues/6476),
[#3661](https://github.com/halide/Halide/issues/3661)), undocumented in the API.

### Interaction with `specialize`

Wrappers and clones are keyed by **consumer Func**, with no notion of a
specialization branch. A single `f.in(g)` / `f.clone_in(g)` wrapper is read by `g`
in **all** of `g`'s specialization branches (§15) — there is no per-branch
wrapper. Correspondingly the consumer argument must be a **`Func`**: a
`g.specialize(cond)` handle is a `Stage`, not a `Func`, so it **cannot be passed**
to `in`/`clone_in` at all (it does not compile) — you cannot "wrap only one
branch." This is the same one-schedule-per-Func fact behind §15's note that a
producer cannot be scheduled per consumer branch. (If instead the *wrapped* Func
`f` is the one specialized, nothing special happens here: consumers read `f`, and
`f`'s branches live inside its own `produce`, per §15.)

The two directives differ in what they carry over from a specialized wrapped Func.
A **clone** is a *deep copy* of the wrapped Func's whole state — definition,
schedule, **and its specializations** — so the clone starts with an independent
copy of those branches
([examples/specialize_clone_inherits.cpp](../examples/specialize_clone_inherits.cpp):
`f` is specialized, and `f.clone_in(g)` prints with the same two branches). An
**`in` wrapper**, by contrast, is a *fresh* pointwise Func (`wrapper(args) =
f(args)`) with its own empty schedule — it does **not** inherit `f`'s
specializations.

### Misc examples

(Examples for the specific surprises above are cited inline in each subsection.)

* [in_basic.cpp](../examples/in_basic.cpp) — `f.in(g)` scheduled `compute_root`
  (`f` → `f_in_g` → `g`); [in_unscheduled.cpp](../examples/in_unscheduled.cpp) — the
  same wrapper left at its default inlines away (nest is just `f` → `g`).
* [in_compute_at.cpp](../examples/in_compute_at.cpp) — the wrapper computed inside
  its consumer (`f_in_g.compute_at(g, y)`).
* [in_multi.cpp](../examples/in_multi.cpp) — `f.in({g1, g2})`, one shared wrapper
  for two named consumers; [in_global.cpp](../examples/in_global.cpp) — `f.in()`,
  one global wrapper redirecting every consumer.
* [in_two_consumers_fix.cpp](../examples/in_two_consumers_fix.cpp) — the positive
  fix for [neg_compute_at_two_consumers.cpp](../examples/neg_compute_at_two_consumers.cpp):
  a per-consumer wrapper can be computed inside its single consumer.
* [clone_basic.cpp](../examples/clone_basic.cpp) — `f.clone_in(g)` with `f` kept by
  another consumer; the clone and `f` share the callee `p` (one `produce p`).
* [tiebreak_visitation_order.cpp](../examples/tiebreak_visitation_order.cpp) — two
  same-prefix producers of one consumer, where the realization-order tie-break's
  *visitation-order* secondary key (§6) decides — the case that arises once
  several wrappers/clones share a name prefix.

### Implementation note

Although the documentation, for simplicity, describes `f.in(g)` or
`f.clone_in(g)` as modifying the consumer `g` to use the
wrapped/cloned `f`, the actual Halide implementation does not mutate
the consumer Funcs at the time you call `in`/`clone_in`: the wrapper
is recorded on `f`, and the consumers' reads are rewritten as a
derived step when the nest is built. Note in particular this
greatly simplifies the interaction between the fallback `f.in()`
wrapper, and other `f` wrappers.

[src_doc: in/clone_in](../src_doc/in_clone_in.md) documents the
identity model and that call-rewrite mechanism in detail.


---

