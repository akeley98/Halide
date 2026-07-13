# Human-added Tasks

Please perform the following restructuring into a "main" loopdoc file and then details in `detail/`.
This will entail significant churn of the section numbers:

* Leave `src_doc/` as is; for now, I made a copy
  `loopdoc_for_src_doc.md` and just have `src_doc/` reference that
  instead.  The `src_doc` was quick-and-dirty help for me, not worth
  paying for renumbering for now.

The real goal of the project is to create a comprehensive,
precise-enough overview of Halide loop nest construction and
associated scheduling and legal/illegal logic.
However, there are many topics that are excessively-detailed
for the general reader.
So I'd like to factor them out to separate documents in `detail/`:

[ ] Detailed rules of `compute_at` legality. Summarize with the
    overall concept in the main doc (off the top of my head, "don't
    realize where the consumers won't be able to read it", "don't
    realize at a place where no consumer exists") and then reference
    the new detail doc for the full rules.
    I give you considerable freedom in how to summarize (my suggestion
    was based on 10 seconds of thinking).

[ ] `compute_with`, fused groups.
    Since "fused group" is part of understanding "how the nest is built",
    please mention that a single realized func without `compute_with`
    is the common simple case of a "fused group": it's one loop nest.

[ ] The transitivity rules and most surprises for `in`/`clone_in`.
    Perfect chance to make the recommendation to only pass
    functions directly consuming `f` to `f.in`/`f.clone_in` when
    possible, and reference the detail doc for rules and surprises
    that can happen if you don't/can't follow that recommendation.
    If any limitations or surprises apply even with that recommendation,
    retain them in the main doc (in particular the `clone_in` twice bug).

[ ] `rfactor`, the feature as a whole.
    Some interactions with `rfactor` will still exist in the main doc
    or other detail docs; reference the new `rfactor` doc in these cases.

[ ] `specialize`, the feature as a whole; same note as `rfactor`.

[ ] `ForType`, the feature as a whole; same note as `rfactor`.

[ ] inlined non-pure functions, all of it.
    Reasonable to mention this in the `compute_inline` section.

[ ] Scrub all mentions of `micro_halide` and the test harness
    infrastructure (e.g. `micro_halide_collapses`) in `loopdoc.md` and
    all other detail docs, and migrate to a dedicated
    `detail/micro_halide.md` file. `micro_halide` only existed to test
    the docs and other readers won't understand what it is.

Simply mechanically relocating entire sections out of the main document
may leave the main document not reading so naturally.
So after relocation, try to repair the main document to read better.

However, I want to keep "Objects and their conceptual state" and
"Putting the algorithm together (how the nest is built)"
comprehensive, so they will refer to concepts not described in the
main document anymore; reference the concepts with markdown links to
the new detail docs (and use markdown links anywhere else the
reference is needed for that matter).

Exercise your best judgment on how to restructure the main document to
compensate for the removed information, and I'll look over the changes
later. I am required to go to lunch break soon (I hate workers rights)
so try to err on the side of working more independently.

This ex-post-factor editing is arguably undermining the dogfooding
done by the micro-agent but I want to proceed with my real goal of
making the core documentation as concise as possible (save tokens /
avoid context rot for future Halide LLM agents), so I may re-run the
whole experiment testing out different edits of the
documentation. This time probably with the micro-agent blinded to the
intended loop nest since I had so many problems with reward hacking /
reverse engineering.
