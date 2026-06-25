## Human-added Tasks

In no particular order, or all at once as one update:

- [ ] Address `Human:` comments in loopdoc.md
- [ ] Break src_doc into many files based on topic.
      `See [§X](src_doc/loop_nest_construction.md)` is not adequate anymore to point to relevant documentation.
- [ ] Look into RealizationOrder.cpp, `check_fused_stages_are_scheduled_in_order`.
      Is this thing just making sure the topological sort for ordering stages in fused groups is not contradictory?
      I'm mainly interested here in strengthening the linkage between the claimed rules in loopdoc.md
      ("cannot fuse `f.s0` into `g.s1` and `f.s1` into `g.s0`") and the documented source code.
