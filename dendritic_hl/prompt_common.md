<!--
  FORMAT CONTRACT (dendritic_hl_lib/prompts.py): the "dh_hl prompt" command
  assembles either the main or the sub agent prompt from this file.  Rules:
  * Text outside any fence is COMMON: it goes into both prompts.
  * A fence is an HTML comment whose only word is "main" (or "sub"); it opens a
    region for that audience, closed by a matching "end main" ("end sub") HTML
    comment.  See the fences used in the body below.
  * Fences must not nest, and every opener needs a matching closer.
  * Single word HTML comments are reserved for fences; use multi word comments
    for maintainer notes.  Comments are stripped from the emitted prompt.
-->
# Dendritic Halide Harness: Agents Prompt

This is an instruction for all agents.

<!-- main -->
This is an instruction for a main agent.
<!-- end main -->

<!-- sub -->
This is an instruction for a sub agent.
<!-- end sub -->

This is an instruction for all agents.
