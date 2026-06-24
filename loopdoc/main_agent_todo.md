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
- [x] RESOLVED (human): illegal schedules killed example binaries via an UNCAUGHT
      `CompileError` -> std::terminate -> SIGABRT, swallowing the real diagnostic
      (only the generic libc++abi line survived). Established it was a clean
      pre-print throw (no partial nest emitted), not an unclean mid-impl exit, and
      the partial-nest worry is moot anyway (canonicalize.py only runs when BOTH
      backends exit 0). Human added `micro_halide::CompileError` + changed the
      example structure to a try/catch (README "Example structure change"); my
      duplicate repro example was removed. Old examples need not be retrofitted.
- [ ] Upstream (outside this repo, the user's call): the `Function::deep_copy`
      header comment in `../src/Function.h` ("recursively deep copies all called
      functions") is misleading for the member in isolation — the recursion is the
      free `deep_copy(outputs, env)` + `substitute_calls` protocol. See src_doc §13
      verdict. Worth a comment fix in Halide proper if/when touching that file.

## Notes / gotchas (standing reminders)

- canonicalize.py keys Func identity on the printed NAME, not on first-appearance
  position: `func_id[name] = F{len}` (canonicalize.py ~151). So two DISTINCT
  micro_halide Funcs that print the same name are merged into one positional id
  (and one structurally-different func can be masked). This bit the custom+global
  wrapper case (two `f_in` wrappers collapsed until the custom one was renamed
  `f_in_<consumer>`). Implication for clone/auto-name-heavy milestones
  (`compute_with`, more wrappers): micro_halide must give each distinct Func a
  distinct printed name matching how Halide disambiguates, or tests silently
  conflate funcs. Not a harness bug (Halide always gives distinct names); just a
  naming-discipline requirement to keep in mind.
