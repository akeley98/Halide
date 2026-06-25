# in() / clone_in(): wrappers and clones

_Part of the [src_doc set](README.md); sections keep their global numbers (§1–§14), and cross-file references are written as "§N"._

## 13. in() / clone_in(): wrappers and clones

Backs loopdoc's wrappers section (`in()` / `clone_in()`, not yet written at the
time of this note). This section is deliberately detailed because the
machinery is subtle and easy to break when maintaining Halide. Two questions
drive it: (a) how the compiler tells apart Funcs that *look* like they share a
name, and (b) what state `f.in(fs)` / `f.clone_in(fs)` actually mutates — in
particular, what (if anything) happens to the consumer Funcs in `fs`.

### API entry points

`Func::in(const Func&)`, `in(const vector<Func>&)`, `in()` (global),
`clone_in(const Func&)`, `clone_in(const vector<Func>&)` (`src/Func.cpp` ~2299–2330)
all funnel into one helper:

    Func get_wrapper(Function wrapped_fn, string wrapper_name,
                     const vector<Func> &fs, bool clone);   // ~2242

with `wrapper_name` built from the wrapped Func's name:
`<wrapped>_in_<consumer>`, `<wrapped>_in`, `<wrapped>_clone_in_<consumer>`, or
`<wrapped>_clone`. `get_wrapper` then appends a uniqueness suffix
`"$" + to_string(wrappers.size())` (~2248), so repeated wrappers of the same
Func get distinct names (`f_in_g$0`, `f_in_g$1`, …).

### (a) How "same-named" Funcs are actually distinguished

They are not same-named. A `Function` is a handle to a `FunctionContents`
addressed by a `FunctionPtr` (a pointer into a `FunctionGroup` plus an index)
**and** carries a unique `name` string. A wrapper/clone is a *new, distinctly
named* Function:

* `new_function_in_same_group(name)` (`src/Function.cpp` ~1219) appends a fresh
  member to the wrapped Func's `FunctionGroup` and returns a `FunctionPtr` to it.
  The **group** is purely a storage/lifetime device: mutually-referencing
  Functions live in one group so within-group edges can be *weak* `FunctionPtr`s,
  which is how Halide avoids reference cycles among a Func and its wrappers. Group
  membership is **not** identity; the `name` (and the `FunctionPtr` it resolves
  to) is.
* The pretty `f.in(g)` string is only a *profiler display name*
  (`set_profiler_display_name`, used in `get_wrapper` ~2267) for tracing output.
  It is cosmetic and never used as identity.
* `print_loop_nest` prints `simplify_func_name(name)` (`src/PrintLoopNest.cpp`
  ~55): it keeps the Func name, drops the `.sN.` stage tag, and truncates at the
  first `$`. So `f_in_g$0` prints as `f_in_g`. (The loopdoc harness then maps
  every distinct name to a positional id, so what is actually verified is that
  the wrapper is a *separate Func node* in the nest, not its spelling.)

### (b) What state changes — and, crucially, what does NOT

`in()/clone_in()` do **not** modify the consumer Funcs in `fs`. The only Func
mutated at call time is the **wrapped** Func, plus creation of the new wrapper:

1. **The wrapper Func is built** (`get_wrapper` ~2257):
   * `create_in_wrapper` (~2169): a fresh *pure* Func whose single definition is
     `wrapper(args) = wrapped(args)` — a pointwise identity that reads the wrapped
     Func. That is the whole body; it is what makes an `in` wrapper a thin
     "caching"/redirection layer.
   * `create_clone_wrapper` (~2176): `deep_copy`s the wrapped Func's **own**
     `FunctionContents` (its init/update `Definition`s, schedule, specializations,
     reduction domains) into the new member, then `substitute_calls` remaps the
     clone's **self-references** to point at the clone itself (weakened). A clone
     is an independent duplicate of *that one Func's* definition + schedule +
     storage; an `in` wrapper is a one-line reader of the original.
     **Its callees are NOT duplicated.** The clone's copied definition expressions
     still hold the *same* `FunctionPtr`s to whatever the original called, so the
     clone reads the *shared* producers. Two independent checks confirm this:
       - **Produce count.** With a 2-level callee chain `q <- p <- f`,
         `f.clone_in(g)` (everything `compute_root`) prints exactly one
         `produce q` and one `produce p`; the clone `f_clone_in_g` reads the same
         `p`. A recursive callee copy would print each twice.
       - **Legality discriminator.** With `f(x)=p(x)`, `f.clone_in(g)`, then
         `p.compute_at(f, x)`, Halide rejects the schedule and its own diagnostic
         lists the uses of `p`: *"`f_clone_in_g$0` uses p"* **and** *"`f` uses
         p"*, with the only legal location `p.compute_root()`. If the clone had a
         private copy of `p`, then `p` would be used by `f` alone and
         `p.compute_at(f, x)` would be legal. (It is illegal, so `p` is shared.)
     Do **not** mis-cite `Func::clone_in`'s "Only this Func is cloned … the
     intermediate Funcs along the path are not" for this: that sentence is about
     the transitive *caller* chain (the Funcs *between* `fs` and the wrapped Func,
     e.g. `sum()`'s anonymous reduction Func), not the wrapped Func's callees. It
     happens to be *consistent* with callee-sharing but is not evidence for it.
     The evidence is the two checks above (and the source trace in the verdict at
     the end of this section).

2. **The mapping is recorded on the WRAPPED Func** (`add_wrapper`,
   `src/Function.cpp` ~1229): it inserts into `wrapped.func_schedule.wrappers()`,
   a `map<string, FunctionPtr>` keyed by **consumer name** (`src/Schedule.cpp`
   ~443); the empty key `""` denotes a *global* wrapper. `add_wrapper` also
   (i) **freezes** the wrapper (`wrapper.freeze()`) so its definition/schedule can
   no longer be edited — this is why you may schedule the returned handle but not
   redefine it — and (ii) **weakens** the `FunctionPtr`s in both directions (the
   map entry, and the wrapper's back-references via `WeakenFunctionPtrs`) to keep
   the group acyclic for refcounting.

   The consumer Funcs in `fs` are **untouched** here. Nothing in `g`'s
   `FunctionContents` changes when you call `f.in(g)`.

3. **`fs` is first normalized to direct callers** (`resolve_transitive_callers`
   ~2219, via `collect_direct_callers_of` ~2193): each `f` in `fs` is replaced by
   the set of Funcs that *directly* call the wrapped Func on a path down from `f`.
   So `f.in(h)` where `h` reaches `f` only through `g` actually registers the
   wrapper under `g` (the direct caller). A Func with no static path to the
   wrapped Func is left as-is (the wrapper is registered under its own name and
   simply never triggers).

### When `fs` is actually rewired: `wrap_func_calls` at lower time

The call substitution is **deferred** to a lowering pass, `wrap_func_calls`
(`src/WrapCalls.cpp`), which `print_loop_nest` runs explicitly
(`src/PrintLoopNest.cpp` ~184; `Lower.cpp` ~164 for the real pipeline). Operating
on the *environment* (a `map<name, Function>` for this realization — a working
copy, not the user's handles):

1. For every Func in `env` and each entry of its `wrappers()` map it builds
   `func_wrappers_map : consumer FunctionPtr -> { wrapped FunctionPtr -> wrapper
   FunctionPtr }`:
   * **custom** wrapper (key = consumer name): substitution registered for that
     consumer only;
   * **global** wrapper (key `""`): registered for *every* Func except the wrapped
     Func itself and the wrapped Func's own wrappers (so the wrapper still reads
     the original), and except consumers that already have a custom wrapper for
     this wrapped Func (custom takes precedence).
2. For each consumer it calls `Function::substitute_calls(substitutions)`
   (`src/Function.cpp` ~1265), which walks the consumer's IR and rewrites every
   `Call` whose `func` is the wrapped Func to instead name the wrapper
   `FunctionPtr`. **This is the only place a consumer's body changes**, and it
   happens on the lowering-time environment copy.
3. `validate_custom_wrapper` (~53) then asserts each custom wrapper's consumer
   really did call the wrapped Func; otherwise `user_error` "Cannot wrap … does
   not call …". This is the `f.in(g)` where `g` never reads `f` error, checked
   *after* substitution so chained wrappers (`f.in(g).in(g)`) validate correctly.

Wrappers are pulled into the environment in the first place by
`populate_environment` / `FindCalls` with `include_wrappers` set: a Func's
`wrappers()` targets are inserted into `env` (`src/FindCalls.cpp` ~76). So a
wrapper is an ordinary Func for realization-order and loop-nest purposes — a
producer of each consumer it was inserted for, and itself a consumer of the
wrapped Func.

Net effect for `f.in(g)` with `g(x) = f(x) + f(x+1)`, all `compute_root`
(verified): realization order is `f`, then `f_in_g`, then `g`; `g`'s two reads of
`f` become reads of `f_in_g`; `f_in_g(x) = f(x)` reads `f`. The wrapper is a
normal node in the nest.

### Implication for micro_halide (representation note)

The Halide design answers the "do I have to hunt down and rewrite every consumer
that references `f`?" worry: **no.** The wrapper relationship is stored once, on
the *wrapped* Func, keyed by consumer name, and the call rewrite is applied as a
*derived* step (`wrap_func_calls`) when the nest is built — not as an eager
mutation of consumer state at `in()` time. A micro_halide that mirrors this
(record `{consumer_name -> wrapper}` on the wrapped Func; resolve producer→wrapper
redirection while walking producers during nest construction) needs no
tree-search over consumer `shared_ptr<FuncContents>` at `in()` time. The eager
alternative — rewriting every consumer's producer pointers when `in()` is called
— is *not* what Halide does and is the churn worth avoiding.

### Verdict on the `Function::deep_copy` header comment

The header comment (`src/Function.h`) on `Function::deep_copy` reads:

> Deep copy this Function into 'copy'. It recursively deep copies all called
> functions, schedules, update definitions, extern func arguments,
> specializations, and reduction domains. … This method also takes a map of
> <old Function, deep-copied version> as input and would use the deep-copied
> Function from the map if exists instead of creating a new deep-copy …

**Verdict: the comment is accurate for the method's intended *whole-pipeline*
use, but misleading about the method *in isolation* — the member
`Function::deep_copy` does not by itself recurse into or copy called functions.**
What the body actually does (`src/Function.cpp` ~497):

* It copies *this* Function's own components: scalar fields, `func_schedule`
  (`FuncSchedule::deep_copy`), `init_def` and each update via
  `Definition::get_copy()`, and extern arguments. `get_copy()`
  (`src/Definition.cpp` ~120) copies the `Definition`'s `values`/`args` `Expr`s
  by plain assignment — and copying an `Expr` is a shallow `IntrusivePtr` share,
  so every `Call` node keeps the **same** `FunctionPtr` to the original callee.
  No callee `FunctionContents` is created here.
* `copied_map` is *consulted* (not populated with new copies) in exactly one
  place inside the method: `deep_copy_extern_func_argument_helper` (~470), which
  looks up an extern-arg callee and `internal_assert`s it is **already** in the
  map. Regular `Call` expressions are not remapped by this method at all.

The "recursively … all called functions" behavior is realized by the **free
function** `deep_copy(const vector<Function>&, const map<string,Function>&)`
(`src/Function.cpp` ~1304), the *caller* that the comment tacitly assumes: it
pre-seeds `copied_map` with an empty copy of **every** Function in the
environment, calls the member `deep_copy` on each, and *then* runs
`substitute_calls(copied_map)` to repoint every `Call` to its copy. The
recursion/coverage is that caller's loop plus the separate `substitute_calls`
pass — not logic inside the member. So the comment describes the *cooperating
protocol's* end-to-end effect and pins it on the member method.

This is exactly why `clone_in` shares callees: `create_clone_wrapper` drives the
member `deep_copy` with a `copied_map` seeded **only** with the wrapped Func's
self-reference, and runs `substitute_calls` for **only** `{wrapped -> clone}`.
With no callees in the map and no env-wide substitution, the copied definition's
`Call`s keep pointing at the originals — the clone shares them. The
`Function::deep_copy` comment overstates the member's behavior.

(The user's hypothesis — that "copying a function" has a subtler internal meaning
than a scheduling-visible copy — is essentially right: the member copies one
Func's *structure*, and "all called functions" is achieved only when a caller
supplies the full `copied_map` and a follow-up `substitute_calls`.)

A separate caution, noted above: the `Func::clone_in` user-doc phrase "the
intermediate Funcs along the path are not [cloned]" is about the transitive
*caller* chain, **not** callees, so it is not independent evidence here. The
callee-sharing claim rests on the source trace plus the two empirical checks in
part (b): a single `produce p`/`produce q` over a 2-level callee chain, and
Halide's own legality diagnostic naming both `f` and `f_clone_in_g$0` as users
of the shared `p`.

**Confidence: high (~0.95).** Grounded in: the member body (no callee creation),
`Definition::get_copy` (shallow `Expr`/`FunctionPtr` share), the free
`deep_copy` + `substitute_calls` protocol, `create_clone_wrapper`'s self-only
remapping, and **two** empirical discriminators (produce-count and the
`compute_at` legality error that explicitly lists the clone as a user of the
shared callee). Residual uncertainty: I did not line-by-line audit
`FuncSchedule::deep_copy` or specialization copying for some hidden
Function-creating path, but the empirical results rule out callee duplication
along the `clone_in` path regardless, so any such path would not change the
verdict for the behavior that matters here.
