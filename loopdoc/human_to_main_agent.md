## Human-added Tasks

In no particular order, or all at once as one update:

- [x] Address `Human:` comments in loopdoc.md
      (all four §14 comments resolved; the deferred topological-sort question
      answered inline + in src_doc/compute_with.md.)
- [x] Break src_doc into many files based on topic.
      loop_nest_construction.md split into overview / compute_at_and_loops /
      storage / transforms / update_definitions / rfactor / in_clone_in /
      compute_with, indexed by src_doc/README.md. loopdoc refs now point to the
      specific topic file; sections keep global numbers §1–§14 as stable IDs.
- [x] Look into RealizationOrder.cpp, `check_fused_stages_are_scheduled_in_order`.
      Yes -- it is an early per-Func/per-parent acyclicity guard (parent-stage
      indices must be non-decreasing as f's stages advance; equal only for
      consecutive f-stages). One of three cooperating ordering guards. Documented
      in src_doc/compute_with.md and loopdoc §14.
      I'm mainly interested here in strengthening the linkage between the claimed rules in loopdoc.md
      ("cannot fuse `f.s0` into `g.s1` and `f.s1` into `g.s0`") and the documented source code.
