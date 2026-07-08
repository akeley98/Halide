# Source evidence: loop-nest construction (bootstrap subset)

These files back the claims in [../loopdoc.md](../loopdoc.md) §§2–16 with
citations into the Halide compiler. Paths are relative to the Halide source
root (`../src` from the loopdoc directory). Line numbers are approximate and
may drift; the surrounding function names are the stable anchors.

The entry point for the loop-nest pseudocode is
`Internal::print_loop_nest(const vector<Function> &)` in
`src/PrintLoopNest.cpp`. It runs the front of the normal lowering pipeline and
then walks the resulting IR `Stmt` with a small `IRVisitor`.

## Index (by topic)

- **§1–§4** — [Overview: lowering entry, defaults, realization order, produce/consume](overview.md)
- **§5–§7** — [A Func's loops, compute_at injection, and loop elision](compute_at_and_loops.md)
- **§8–§9** — [store_at / store_root and hoist_storage](storage.md)
- **§10** — [split / fuse / reorder / tile](transforms.md)
- **§11** — [Update (reduction) definitions: stages](update_definitions.md)
- **§12** — [rfactor](rfactor.md)
- **§13** — [in() / clone_in(): wrappers and clones](in_clone_in.md) ·
  [transitivity: which Funcs are affected](in_clone_in_transitivity.md)
- **§14** — compute_with: fused groups (split by topic):
  [fused_groups](compute_with/fused_groups.md) ·
  [growth](compute_with/growth.md) ·
  [member_sites](compute_with/member_sites.md) ·
  [ordering](compute_with/ordering.md) ·
  [legality](compute_with/legality.md)
- **§15** — [specialize: conditional schedule variants](specialize.md)

Sections are globally numbered §1–§15 across these files (stable IDs). A cross-file reference like "§7" points to the file whose range covers 7 (here, [compute_at_and_loops.md](compute_at_and_loops.md)). (loopdoc §16 — the whole-algorithm synthesis — has no separate src_doc file; its pieces are cited from the topic files above.)

See also [appendix_inline_realized.md](appendix_inline_realized.md).
