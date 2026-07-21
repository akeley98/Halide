# A Func's loops, compute_at injection, and loop elision

_Part of the [src_doc set](README.md); sections keep their global numbers (§1–§14), and cross-file references are written as "§N"._

## 5. A Func's own loops; first arg is innermost

`build_produce_definition` (`src/ScheduleFunctions.cpp` ~1508) emits the loops
for a definition over its dimensions. The dimension list is ordered with the
pure args such that the *first* argument ends up the innermost loop and the
last the outermost — matching the `for c: for y: for x:` ordering for
`f(x, y, c)` (loopdoc §3). The `For` printer:

    // src/PrintLoopNest.cpp, visit(const For *)
    out << get_indent() << op->for_type << " " << simplify_var_name(op->name);
    // ... prints " in [min, max]" only when both are const ...

and the leaf:

    // src/PrintLoopNest.cpp, visit(const Provide *)
    out << get_indent() << simplify_func_name(op->name) << "(...) = ...\n";

confirms the leaf line shape `f(...) = ...` (loopdoc §2).

## 6. compute_at injection point

For a Func with `compute_level == at(site func, var)`, `schedule_functions` finds
the loop in the site func whose name matches the compute level and injects the
producer's realization at that point. The matching is done by the
`compute_level.match(for_loop->name)` test inside the injecting mutator:

    // src/ScheduleFunctions.cpp (~1299)
    if (compute_level.match(for_loop->name)) {
        ...
        _found_compute_level = true;
    }

The realization (`produce`/loops/`consume`) is spliced in as a prefix of that
loop's body, with the remainder of the body becoming the `consume` content.
This backs loopdoc §7's nesting picture and §16 steps 3–4.

`compute_inline()` is not a separate mechanism: `Func::compute_inline()`
(`src/Func.cpp` ~3070) is literally `return compute_at(LoopLevel::inlined());`,
i.e. it just sets the compute level back to the `inlined` default. So it needs no
new emission logic — the resulting nest is §5's pure-inline substitution or §11's
non-pure realize-at-innermost-use. The only related guard is that an `inlined`
compute level forbids a store/hoist level (`store_at`/`store_root`/`hoist_storage`
[_root]), checked at `ScheduleFunctions.cpp` ~2319–2331 — see
[storage](storage.md) — and this fires on `compute_at.is_inlined()` regardless of
whether the Func is pure or non-pure. This backs loopdoc §6's `compute_inline`
paragraph.

### Legality of a compute_at site

Before injecting, `schedule_functions` validates the requested level against the
set of legal sites computed by `ComputeLegalSchedules` (`src/ScheduleFunctions.cpp`).
That visitor walks the loop nest maintaining the current stack of enclosing loop
levels (`sites`) and, at every *use* of the Func, intersects:

    // src/ScheduleFunctions.cpp, ComputeLegalSchedules::register_use (~1936)
    if (!found) { sites_allowed = sites; }      // first use: its enclosing loops
    else {
        // keep only loop levels common to this use and all previous uses
        for (s1 : sites) for (s2 : sites_allowed)
            if (s1.loop_level.match(s2.loop_level)) common_sites.push_back(s1);
        sites_allowed.swap(common_sites);
    }

So `sites_allowed` ends up as the loop levels that **enclose every use** of the
Func (their common ancestors), always including `root`. The requested
`compute_at` is then looked up in that set:

    // src/ScheduleFunctions.cpp (~2333, ~2380)
    for (i : sites) if (sites[i].loop_level.match(compute_at) && ...) compute_idx = i;
    ...
    if (!all_ok()) {
        err << "Func \"" << f.name() << "\" is computed at the following invalid location:\n" ...
            << "Legal locations for this function are:\n" ...   // prints sites_allowed
        user_error << err.str();   // aborts (no exceptions build) / throws CompileError
    }

This is the source basis for loopdoc §7 "When a compute_at is illegal":

* loop does not exist  → requested level matches no `Site` → not found.
* site func is not a consumer → the site func's loops never appear in any use's stack, so
  they are never in `sites_allowed`.
* a consumer lies outside the site → that consumer's use stack does not contain
  the site, so intersection drops it; with two unrelated uses only `root`
  survives.

micro_halide mirrors this with `LoopNestPrinter::validate` (`enclosed_by` =
"the site is a common ancestor of this use"), throwing instead of printing,
which makes the binary exit non-zero exactly as Halide's `user_error` does.

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
sub-region read per site-func iteration, so any pointwise dimension collapses. This
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
§9 (see `examples/compute_at_elided_level.cpp`). Because predicting min == max
requires the full bounds model, loopdoc declares elision via the `micro_halide_collapses`
annotation rather than deriving it; that annotation has no counterpart in the
real compiler (it is a no-op shim, `halide_compat/halide_compat.h`).
