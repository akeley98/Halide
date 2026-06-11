# Source evidence: loop-nest construction (bootstrap subset)

This file backs the claims in [../loopdoc.md](../loopdoc.md) §§3–8 with
citations into the Halide compiler. Paths are relative to the Halide source
root (`../src` from the loopdoc directory). Line numbers are approximate and
may drift; the surrounding function names are the stable anchors.

The entry point for the loop-nest pseudocode is
`Internal::print_loop_nest(const vector<Function> &)` in
`src/PrintLoopNest.cpp`. It runs the front of the normal lowering pipeline and
then walks the resulting IR `Stmt` with a small `IRVisitor`.

## 1. The output Func is forced to root

`print_loop_nest` explicitly schedules every output Func at root before
lowering:

    // src/PrintLoopNest.cpp, print_loop_nest()
    for (const Function &f : outputs) {
        Func(f).compute_root().store_root();
    }

This is why the output never inlines and always appears as the outermost
`produce` (loopdoc §3, §4, §8 step 1). In the real `realize`/`compile` path the
same effect comes from the output simply being the realized buffer.

## 2. The default schedule is "inlined"

A Func's compute and store levels default to `LoopLevel::inlined()`:

    // src/Schedule.cpp, FuncScheduleContents ctor (~line 249)
    : store_level(LoopLevel::inlined()),
      compute_level(LoopLevel::inlined()),
      hoist_storage_level(LoopLevel::inlined()) { ... }

`schedule_functions` (in `src/ScheduleFunctions.cpp`) treats an inlined Func by
substituting its definition into its callers rather than giving it a loop nest;
`is_inlined()` / `is_root()` are checked throughout (e.g. the
`InjectRealization`/inlining logic gated on
`level.is_inlined() || level.is_root()` near line 1003, and the inlined branch
at ~1977 / 2298). This backs loopdoc §3: unscheduled Funcs vanish from the nest.

## 3. Realization order

The topological producers-before-consumers order is computed by

    // src/RealizationOrder.cpp
    pair<vector<string>, vector<vector<string>>>
    realization_order(const vector<Function> &outputs,
                      map<string, Function> &env);

`print_loop_nest` calls it (`auto [order, fused_groups] =
realization_order(outputs, env);`) and `schedule_functions` injects each
function's realization in that order. This backs loopdoc §4 ("realization
order") and §8 step 2. Inlined Funcs remain in `env` and so still contribute
edges to the ordering even though they get no realization.

## 4. produce / consume nesting

The `produce` and `consume` IR nodes are created in `build_realization` /
`InjectFunctionRealization` in `src/ScheduleFunctions.cpp`:

    // src/ScheduleFunctions.cpp (~1866)
    // Add the producer nodes.
    for (const auto &i : funcs) {
        producer = ProducerConsumer::make_produce(i.name(), producer);
    }
    // Add the consumer nodes.
    for (size_t i = 0; i < funcs.size(); i++) {
        if (!is_output_list[i]) {
            consumer = ProducerConsumer::make_consume(funcs[i].name(), consumer);
        }
    }
    if (is_no_op(consumer)) {
        return producer;             // first/output realization: no consume
    } else {
        return Block::make(producer, consumer);
    }

Two facts in loopdoc §4 come directly from here:

* The **output gets no `consume`**: `is_output_list[i]` suppresses it (and for
  the very first realization the consumer is a no-op and is dropped).
* Each realization is `Block(produce, consume)` with the consume wrapping the
  *rest* of the program — which is how the chain in loopdoc §4 nests.

The visitor that prints these nodes:

    // src/PrintLoopNest.cpp, visit(const ProducerConsumer *)
    out << (op->is_producer ? "produce " : "consume ")
        << simplify_func_name(op->name) << ":\n";

## 5. A Func's own loops; first arg is innermost

`build_produce_definition` (`src/ScheduleFunctions.cpp` ~1508) emits the loops
for a definition over its dimensions. The dimension list is ordered with the
pure args such that the *first* argument ends up the innermost loop and the
last the outermost — matching the `for c: for y: for x:` ordering for
`f(x, y, c)` (loopdoc §5). The `For` printer:

    // src/PrintLoopNest.cpp, visit(const For *)
    out << get_indent() << op->for_type << " " << simplify_var_name(op->name);
    // ... prints " in [min, max]" only when both are const ...

and the leaf:

    // src/PrintLoopNest.cpp, visit(const Provide *)
    out << get_indent() << simplify_func_name(op->name) << "(...) = ...\n";

confirms the leaf line shape `f(...) = ...` (loopdoc §2).

## 6. compute_at injection point

For a Func with `compute_level == at(host, var)`, `schedule_functions` finds
the loop in `host` whose name matches the compute level and injects the
producer's realization at that point. The matching is done by the
`compute_level.match(for_loop->name)` test inside the injecting mutator:

    // src/ScheduleFunctions.cpp (~1299)
    if (compute_level.match(for_loop->name)) {
        ...
        _found_compute_level = true;
    }

The realization (`produce`/loops/`consume`) is spliced in as a prefix of that
loop's body, with the remainder of the body becoming the `consume` content.
This backs loopdoc §7's nesting picture and §8 steps 3–4.

## 7. Why a compute_at Func can emit fewer loops than it has dimensions

Bounds inference computes, for each realization, the *region* of the Func
required at that point in the nest. A dimension needed at only a single point
yields a loop whose min equals its max. The simplifier then removes such a loop
entirely, replacing it with a `let` that binds the loop variable to the single
value:

    // src/Simplify_Stmts.cpp, Simplify::visit(const For *)  (~282)
    } else if (equal(new_min, new_max) &&
               op->device_api == DeviceAPI::None) {
        // Loop body runs exactly once
        return mutate(LetStmt::make(op->name, new_min, new_body));
    }

`print_loop_nest` runs `simplify(s)` as its last step, so these extent-1 loops
are gone before printing. A root Func is required over its full output region,
so none of its loops collapse; a `compute_at` Func is required only over the
sub-region read per host iteration, so any pointwise dimension collapses. This
is the source-level basis for the caveat in loopdoc §7: the loop *count* of a
`compute_at` Func is a function of bounds inference, not just its
dimensionality.

To see it directly, `HL_DEBUG_CODEGEN=2` dumps the bounds and the pre-simplify
loop nest; compare the `for` over a collapsed dimension (min == max) before
`simplify` with its absence afterwards.

Note the elision is purely a *printing/simplification* effect: the producer's
realization is injected at the loop level during `schedule_functions` (before
simplify), so when the loop is later collapsed into a `LetStmt`, anything that
was injected at that level — including a `compute_at` child of the collapsed
loop — stays at that position; only the `For` node disappears. This is the
source-level basis for "an elided loop is still an injection site" in loopdoc
§7 (see `examples/compute_at_elided_level.cpp`). Because predicting min == max
requires the full bounds model, loopdoc declares elision via the `micro_halide_collapses`
annotation rather than deriving it; that annotation has no counterpart in the
real compiler (it is a no-op shim, `halide_compat/halide_compat.h`).
