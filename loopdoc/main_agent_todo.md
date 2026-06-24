# Main-agent scratch ledger

A private working ledger for the **main agent**: cross-cutting reminders,
deferred/backlog items, and process notes that must outlive an LLM context
window (compaction) but do not belong in any of the curated records.

## Charter — what goes here vs. elsewhere

IN scope:
  * deferred / backlog work ("revisit when X happens")
  * cross-cutting reminders that span milestones
  * process notes / methodology tweaks to remember

OUT of scope (hard rule — keep this file from rotting into a second knowledge base):
  * behavioral facts about Halide / the loop nest      -> loopdoc.md
  * source-level mechanics / compiler internals          -> src_doc/
  * milestone status (started / WIP / done)              -> progress.txt milestones
  * concrete "loopdoc failed to answer X" questions      -> progress.txt DISCOVERED DOC GAPS

The micro-agent never reads this file; it is not part of the dogfooding input.
Settled decisions and findings still go in git commit messages (durable).

Convention: `- [ ]` open, `- [x]` done (leave done items a while as a record,
then prune). Add a one-line `why` so future-me knows the trigger.

## Backlog

- [ ] `DimData` needs a per-dimension `for_type` field before the parallelism
      milestone (`parallel`/`vectorize`/`unroll`/`gpu`). The canonicalizer keeps
      loop *type*, so each dim must carry it. This is the one micro_halide
      representation change we already know is coming; fold it in when that
      milestone starts (and it is also what would unblock the deferred
      split-RVar + rfactor case — see progress.txt DISCOVERED DOC GAPS).
- [ ] Prune the `rfactor` and `Update definitions` milestone NOTES in progress.txt
      the same way the `in`/`clone_in` notes were trimmed: they still carry verbose
      "MODEL: ..." behavioral digests that are now mirrored in loopdoc/src_doc.
      Deferred because they are completed-milestone history; do when convenient.
- [ ] Upstream (outside this repo, the user's call): the `Function::deep_copy`
      header comment in `../src/Function.h` ("recursively deep copies all called
      functions") is misleading for the member in isolation — the recursion is the
      free `deep_copy(outputs, env)` + `substitute_calls` protocol. See src_doc §13
      verdict. Worth a comment fix in Halide proper if/when touching that file.
