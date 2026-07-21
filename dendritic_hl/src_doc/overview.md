# Overview: lowering entry, defaults, realization order, produce/consume

_Part of the [src_doc set](README.md); sections keep their global numbers (§1–§14), and cross-file references are written as "§N"._

## 1. The output Func is forced to root

`print_loop_nest` explicitly schedules every output Func at root before
lowering:

    // src/PrintLoopNest.cpp, print_loop_nest()
    for (const Function &f : outputs) {
        Func(f).compute_root().store_root();
    }

This is why the output never inlines and always appears as the outermost
`produce` (loopdoc §5, §6, §16 step 1). In the real `realize`/`compile` path the
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
at ~1977 / 2298). This backs loopdoc §5: pure unscheduled Funcs vanish from the nest.

## 3. Realization order

The topological producers-before-consumers order is computed by

    // src/RealizationOrder.cpp
    pair<vector<string>, vector<vector<string>>>
    realization_order(const vector<Function> &outputs,
                      map<string, Function> &env);

`print_loop_nest` calls it (`auto [order, fused_groups] =
realization_order(outputs, env);`) and `schedule_functions` injects each
function's realization in that order. This backs loopdoc §6 ("realization
order") and §16 step 2. Inlined Funcs remain in `env` and so still contribute
edges to the ordering even though they get no realization.

### Sibling tie-break

`realization_order` builds, for each Func, the list of its direct callees, then
runs a post-order DFS (`realization_order_dfs`). The *order siblings come out
in* is fixed by sorting each callee list before the DFS:

    // src/RealizationOrder.cpp, sort_funcs_by_name_and_counter (~256)
    // sort key per Func: (prefix, visitation_counter, full_name)
    string prefix = split_string(full_name, "$")[0];
    while (!prefix.empty() && std::isdigit(prefix.back())) prefix.pop_back();
    ...
    std::sort(items.begin(), items.end());   // tuple<prefix, counter, name>

The comment there explains the intent: make the order resistant to
`unique_name` churn so that machine-generated schedules stay valid. The
`counter` is the Func's first-visitation index, from

    // src/RealizationOrder.cpp
    map<string, uint64_t> compute_visitation_order(const vector<Function> &outputs) {
        vector<Function> funcs = called_funcs_in_order_found(outputs);
        ...
    }

and `called_funcs_in_order_found` (`src/FindCalls.cpp`) is a pre-order DFS from
the outputs (`populate_environment_helper` inserts a Func, then recurses into
its calls in IR order). This is the source basis for loopdoc §6's tie-break
rule (prefix, then visitation order, then full name) — and explains why the
left-to-right order of a defining expression does *not* decide which sibling
producer is realized first (see
`examples/tiebreak_realization_order.cpp`). micro_halide mirrors this in
`LoopNestPrinter::sort_key` / `compute_visit_order`.

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

Two facts in loopdoc §6 come directly from here:

* The **output gets no `consume`**: `is_output_list[i]` suppresses it (and for
  the very first realization the consumer is a no-op and is dropped).
* Each realization is `Block(produce, consume)` with the consume wrapping the
  *rest* of the program — which is how the chain in loopdoc §6 nests.

The visitor that prints these nodes:

    // src/PrintLoopNest.cpp, visit(const ProducerConsumer *)
    out << (op->is_producer ? "produce " : "consume ")
        << simplify_func_name(op->name) << ":\n";
