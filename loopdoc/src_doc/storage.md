# store_at / store_root and hoist_storage

_Part of the [src_doc set](README.md); sections keep their global numbers (§1–§14), and cross-file references are written as "§N"._

## 8. store_at / store_root: the `store` node

Backs loopdoc §8.

### Where the storage (Realize) node is injected

`schedule_functions` injects each Func's storage as a `Realize` node at its
**store level**, separately from the `produce`/`consume` (ProducerConsumer) and
loops that go at its compute level. In the For-loop mutator:

    // src/ScheduleFunctions.cpp (~1307)
    if (funcs[i].schedule().store_level().match(for_loop->name)) {
        ...
        body = build_realize_function_from_group(body, i);   // wrap body in Realize
    }

    // build_realize (~1394)
    s = Realize::make(name, func.output_types(), memory_type, bounds, const_true(), s);

So the `Realize` wraps everything from the store-level loop down (including the
site-func loops between the store and compute levels, and the `produce`/`consume`
spliced in deeper at the compute level). `store_root()` is `LoopLevel::root()`,
the outermost level, so its `Realize` ends up wrapping the whole pipeline body —
this is why loopdoc §8's `store_root` node prints outside the output's
`produce`.

### Why the `store` line appears only when store != compute

The `Realize` node is *always* present (storage must be allocated somewhere),
but `print_loop_nest` only prints a `store` line when the store level differs
from the compute level:

    // src/PrintLoopNest.cpp, visit(const Realize *)
    if (it != env.end() &&
        !(it->second.schedule().store_level() ==
          it->second.schedule().compute_level())) {
        out << "store " << simplify_func_name(op->name) << ":\n";
        indent += 2; op->body.accept(this); indent -= 2;
    } else {
        op->body.accept(this);   // no store line; just recurse
    }

This is the source basis for "the `store` node is shown only when store !=
compute" (loopdoc §8), and for `store_root().compute_root()` printing no store
node (both at root, so equal).

### Per site-func stage (store node follows the produce)

`store_level().match(for_loop->name)` is tested on the loop names actually
emitted, which are per stage (one `v` loop per stage of the site func — the
stage-wildcard `match` accepts any of them, §11). The `Realize` is injected into
the same mutated stage body that received the func's `produce`/`consume`, and that
injection is itself per-stage and use-gated (§7: a producer is realized only in
the site-func stages whose body uses it). So when the site func has several
stages, the `store` node lands at `v` only in the stages that actually compute the
func, not in a site-func stage that merely owns a matching `v` loop. Backs loopdoc
§8's "per site-func stage" paragraph and `store_at_update_stage` (producer read only in
the update stage) / `rfactor_intm_store_at` (intermediate stored at the merge
stage, absent from the pure stage). (micro_halide initially emitted the store
node in the first stage owning the store-level loop, regardless of where the
func is computed — see progress.txt.)

### Legality

`validate_schedule` (~2285) looks up the requested store and compute levels in
the list of legal `Site`s (the enclosing-loop stack intersection from
`ComputeLegalSchedules`, see §7) and requires the store site to be found
*before* (i.e. outside) the compute site:

    // src/ScheduleFunctions.cpp (~2333)
    if (sites[i].loop_level.match(store_at) && hoist_storage_idx >= 0) store_idx = i;
    if (sites[i].loop_level.match(compute_at) && store_idx >= 0 ...)   compute_idx = i;
    ...
    if (!all_ok()) user_error << "... is computed at the following invalid location ...";

Because `compute_idx` is only set once `store_idx >= 0`, a store level inside
the compute level can never satisfy both — hence loopdoc §8's "store must
enclose compute" and `neg_store_inside_compute.cpp`. Separately, a store level
on an inlined Func is rejected up front:

    // src/ScheduleFunctions.cpp (~2300)
    user_error << "Func \"" << f.name() << "\" is scheduled store_at(), but is "
               << "inlined. Funcs that use store_at must also call compute_at.\n";

which backs `neg_store_at_inlined.cpp`.

## 9. hoist_storage / hoist_storage_root: invisible to print_loop_nest

Backs loopdoc §8's hoist-storage subsection.

### No print effect

`hoist_storage` introduces a third level (the hoist-storage level) that only
controls where the physical allocation is emitted; it does not trigger sliding
window. `print_loop_nest`'s visitor (`src/PrintLoopNest.cpp`) has handlers only
for `For`, `Realize` (the `store` line, gated on store != compute), 
`ProducerConsumer`, `Provide`, and `LetStmt`. There is no handler keyed on the
hoist-storage level, so changing it does not change the printed nest. (In the
full lowering pipeline the hoist level affects where the allocation is
physically placed, but that is past what `print_loop_nest` shows.) This backs
loopdoc §8: a legal `hoist_storage` schedule prints identically to the same
schedule without it.

### Legality

`validate_schedule` (`src/ScheduleFunctions.cpp` ~2298) rejects `hoist_storage`
on an inlined Func:

    // ~2305
    if (hoist_storage_at.is_root()) {
        user_error << "... scheduled hoist_storage_root(), but is inlined. ...";
    } else if (!hoist_storage_at.is_inlined()) {
        user_error << "... scheduled hoist_storage(), but is inlined. "
                   << "Funcs that use hoist_storage_root must also call compute_at.";
    }

and requires the hoist level to enclose the store level (which encloses
compute), via the same `Site` scan used for store/compute (~2333): the indices
are resolved outermost-first as

    hoist_storage_idx found  ->  then store_idx (requires hoist found)
                             ->  then compute_idx (requires store found)

so a hoist level inside the store/compute level can never satisfy all three and
yields the "invalid location" error. This backs `neg_hoist_at_inlined.cpp` and
`neg_hoist_inside_compute.cpp`. By default the hoist level coincides with the
store level, so an unset hoist level adds no constraint.
