# Loop types (`ForType`): `serial`, `parallel`, `vectorized`, `unrolled`, GPU

Detail companion to the main [loopdoc.md](../loopdoc.md); section references "§N" point to that document.

Each loop carries a **type** (and, for GPU, a device) shown as the first token on its loop line — independent of the loop *structure* the other directives build.

---

Everything so far builds the loop *structure* — how many loops, their nesting,
their order. Independently, each loop carries a **type** that decides how its
iterations run. The type is a per-dimension property (it rides on the dimension,
§3), and it is the first token on the loop line (§2):

```
for <var>:             # ForType::Serial   — the default
parallel <var>:        # ForType::Parallel
vectorized <var>:      # ForType::Vectorized
unrolled <var>:        # ForType::Unrolled
gpu_block <var><API>:  # ForType::GPUBlock  — plus a <device_api> suffix
gpu_thread <var><API>: # ForType::GPUThread
gpu_lane <var><API>:   # ForType::GPULane
```

This document ignores loop-variable names and constant bounds (§2), but the
**type token and the `<device_api>` suffix are significant**. So a loop's
observable identity is `(type, device, position in the nest)`. Serial is the
default and prints as plain `for`; `serial(v)` resets a dimension back to it.

### Setting a whole dimension's type

`f.parallel(v)`, `f.vectorize(v)`, `f.unroll(v)`, `f.serial(v)` set the type of
an existing dimension `v` in place — no new loop. They apply **per stage** like
the §9 transforms (`f.update(i).parallel(v)` types update stage `s(i+1)` only).
See [examples/fortype_parallel.cpp](../examples/fortype_parallel.cpp),
[examples/fortype_vectorize.cpp](../examples/fortype_vectorize.cpp),
[examples/fortype_unroll.cpp](../examples/fortype_unroll.cpp), and
[examples/fortype_update_stage.cpp](../examples/fortype_update_stage.cpp) (only the
update stage's loop is typed).

### The factor form implies a `split` — and marks a *different* half

`f.vectorize(v, n)`, `f.unroll(v, n)`, `f.parallel(v, n)` first `split(v, v, vi, n)`
(§9) and then type one of the two resulting loops. **Which half differs by
directive:**

* `vectorize(v, n)` and `unroll(v, n)` type the **inner** loop (the width-`n`
  one): outer stays `for`, inner becomes `vectorized`/`unrolled`.
* `parallel(v, n)` types the **outer** loop: outer becomes `parallel`, inner
  stays `for`.

This is where §9's "split inner/outer order is invisible" stops being true: the
two halves now carry different tokens, so the order is observable. `vectorize(x, 8)`
prints `for … : vectorized …:` while `parallel(x, 8)` prints `parallel …: for …:`
— structurally distinct nests.
[examples/fortype_vectorize_split.cpp](../examples/fortype_vectorize_split.cpp) and
[examples/fortype_parallel_split.cpp](../examples/fortype_parallel_split.cpp) are
that contrast. (The `TailStrategy` argument only affects the split's boundary
handling, which needs bounds — out of scope, see below.)

### GPU: type token *plus* a device

The GPU directives set both the type and a `DeviceAPI` (default `Default_GPU`),
which prints as a `<Default_GPU>` suffix on the loop line — the only loop type
that carries a device.

* `gpu_blocks(v[, v2, v3])`, `gpu_threads(...)`, `gpu_lanes(v)` type existing
  dimensions `GPUBlock`/`GPUThread`/`GPULane` — no split.
  ([examples/fortype_gpu_blocks_threads.cpp](../examples/fortype_gpu_blocks_threads.cpp).)
* `gpu_tile(v, vo, vi, n, ...)` is sugar: it `split`s (or `tile`s, in the
  multi-dim overloads) and then makes the block loop(s) `GPUBlock` and the tile
  loop(s) `GPUThread` — outer `gpu_block`, inner `gpu_thread`.
  ([examples/fortype_gpu_tile.cpp](../examples/fortype_gpu_tile.cpp).)
* `gpu(bx, tx, ...)` maps already-existing dims to blocks and threads (no split);
  `gpu_single_thread()` wraps the stage in a single (extent-1) block+thread loop
  pair around the existing loops
  ([examples/fortype_gpu_single_thread.cpp](../examples/fortype_gpu_single_thread.cpp)).

`print_loop_nest` shows GPU loops **raw** — it does *not* run the GPU-specific
lowering passes (`CanonicalizeGPUVars`, `FuseGPUThreadLoops`), so what you
schedule is what prints. GPU legality (a block loop must enclose a thread loop,
warp-size limits on `gpu_lanes`, thread-count bounds) is enforced only during GPU
lowering, which this path skips — so it is **out of scope** here (and needs
bounds analysis micro does not do).

The type and the device are **independent** fields on the dimension: a non-GPU
directive (`serial`/`parallel`/`vectorize`/`unroll`) changes only the type and
leaves any device in place, and there is no directive that clears a device. So
re-typing a GPU dim does *not* undo the GPU-ness — `gpu_threads(x)` then
`vectorize(x)` prints `vectorized x<Default_GPU>`, a typed loop still carrying its
device. (Real Halide would reject such a schedule during GPU lowering, which this
path skips; reproducing that error is out of scope.)

### The type rides the dimension through `split` / `fuse` / `reorder`

Because the type is a property of the dimension, the §9 transforms carry it:

* **`split` / `tile`**: both produced loops **inherit** the source dimension's
  type and device — splitting a `parallel` dim yields two `parallel` loops;
  splitting a `vectorized` dim yields two `vectorized` loops
  ([examples/fortype_split_inherit.cpp](../examples/fortype_split_inherit.cpp)).
* **`fuse(inner, outer, fused)`**: the fused loop takes the **inner** dimension's
  type and device; the outer's type is dropped. Fusing a `parallel` outer with a
  serial inner gives a plain `for`; fusing a `vectorized` inner with a serial
  outer gives a `vectorized` loop
  ([examples/fortype_fuse_inner_wins.cpp](../examples/fortype_fuse_inner_wins.cpp)).
* **`reorder`**: the type stays attached to its dimension as it moves. This is
  the promised second way `reorder` becomes observable (§9): reorder a typed loop
  outward and the token moves with it
  ([examples/fortype_reorder_typed.cpp](../examples/fortype_reorder_typed.cpp)).

### Extent-1 loops collapse — *unless* they are GPU

The §7 loop-elision rule (a loop whose extent is 1 prints no `for` line) is gated
in Halide's simplifier on `device_api == None`: a 1-iteration **serial /
parallel / vectorized / unrolled** loop is removed, but a 1-iteration **GPU**
loop **survives** (a `gpu_block`/`gpu_thread` with a single lane still prints).
That is why `gpu_single_thread()` shows its extent-1 block and thread loops
rather than eliding them.

### `compute_with` requires matching types on the fused dimensions

When two stages are fused with `compute_with` (§14), each pair of shared
dimensions down to the fuse level must have the **same** type (and device) — the
per-pair check compares `for_type`/`device_api`, not just name and count.
Fusing a `parallel` dim with a `vectorized` one is rejected at schedule time
(*"Invalid compute_with: for types of dim N … do not match"*),
[examples/neg_compute_with_fortype_mismatch.cpp](../examples/neg_compute_with_fortype_mismatch.cpp);
when they match, the shared loop carries that one type.

### Out of scope

* **`TailStrategy`** (on the factor forms and `gpu_tile`) — chooses how the split
  tail is handled; observable only through bounds, which this path normalizes away.
* **`atomic()` / `allow_race_conditions()`** and the race-condition legality of a
  `parallel` `RVar` — a legality concern needing associativity/bounds analysis,
  not a loop-nest-structure concern; micro does not model it.
* **GPU legality** and **multiple device APIs** beyond the default (see above).

---

