"""dh_hl command-line entry point: argparse dispatch + help tool."""

import argparse
import os
import re
import subprocess
import sys

from . import locks
from . import prompts
from . import safety
from . import tools
from . import build as build_mod
from .errors import DhHlError
from . import guide_flag
from . import allow_harness_flag

# When allow_harness_flag.enabled is False (a no-harness experiment run), the CLI
# exposes ONLY these commands -- the minimum to stand up a catalog and log a
# no-harness run (begin_experiment.py's new_catalog/disable_problem/new_problem
# and the agent's `experiment build_external`/`add_schedule_node`), plus the
# exec/exec_exclusive escape hatches for running plain commands under the machine
# lock.  Every other tool is turned off (see _build_parser and main()).
_NO_HARNESS_ALLOWLIST = frozenset({
    "experiment", "new_catalog", "disable_problem", "new_problem",
    "set_main_problem", "exec", "exec_exclusive"})

# idea.md is the human-facing spec; `dh_hl help <command>` renders the relevant
# tool section from it so the detailed per-command docs have a single source.
# It sits one level above the package dir (dendritic_hl/idea.md); it is outside
# the package, so a copy run detached from the repo won't find it -- callers
# fall back to the COMMAND_HELP one-liner in that case.
_IDEA_MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "idea.md")

# A synopsis line inside a tool section, e.g. "    dh_hl status -s ..." (possibly
# after a leading "# comment").  The command name is what we key sections by.
# Accepts A-Z so a mixed-case command (e.g. a future PascalCase noun) is still
# parsed and checked against COMMAND_HELP, rather than silently slipping past the
# documented==implemented consistency test.
_SYNOPSIS_RE = re.compile(r"^    (?:# .*)?dh_hl ([A-Za-z_]+)\b")


def _strip_maintainer_lines(lines):
    """Drop lines that are for maintainers, not `dh_hl help` readers."""
    return [ln for ln in lines
            if not ln.strip().startswith("NOTE: [link") and "<!--" not in ln]


def _parse_idea_sections(path=_IDEA_MD):
    """Parse the idea.md "# Tools" section.  Returns `(intro, mapping)`:

    * `intro` — the prose between the "# Tools" heading and the first heading
      below it (the common usage notes shown by `dh_hl help` with no arg).
    * `mapping` — command name -> the text of the "### ... Tool" section that
      documents it, keyed by the commands in each section's *leading* indented
      synopsis block (so a multi-command section maps all its commands to the
      same shared text).

    The idea.md text is first run through `prompts.render_idea_help`, which drops
    the `<!-- impl -->` detail regions (implementer notes) but keeps the
    `<!-- help -->` regions, and strips all other HTML comments.  Returns
    `("", {})` if idea.md can't be read.  See the FORMAT CONTRACT comment above
    "# Tools" in idea.md."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return "", {}
    lines = prompts.render_idea_help(raw).split("\n")

    in_tools = False         # reached the "# Tools" section yet?
    intro_lines = None       # collecting the "# Tools" intro (until next heading)
    intro_captured = []      # the finished intro (saved at that first heading)
    sections = []            # list of (heading, body_lines)
    cur = None
    for line in lines:
        if line.rstrip() == "# Tools":
            in_tools = True
            intro_lines = []
            cur = None
            continue
        if not in_tools:
            continue
        if line.startswith("### "):
            if intro_lines is not None:
                intro_captured = intro_lines  # intro ends at the first heading
                intro_lines = None
            cur = (line, [])
            sections.append(cur)
        elif line.startswith("## ") or line.startswith("# "):
            # A "## ..." group heading (or a new top-level "# ...") ends the
            # intro; the group prose before its first "### " tool section is not
            # part of any tool's help.
            if intro_lines is not None:
                intro_captured = intro_lines
                intro_lines = None
            cur = None
        elif intro_lines is not None:
            intro_lines.append(line)
        elif cur is not None:
            cur[1].append(line)
    if intro_lines is not None:  # "# Tools" had no following heading (degenerate)
        intro_captured = intro_lines

    intro = "\n".join(_strip_maintainer_lines(intro_captured)).strip("\n")

    mapping = {}
    for heading, body in sections:
        # Commands in the leading synopsis block: scan indented lines until the
        # first non-blank, non-indented (prose) line -- later code blocks are
        # examples / cross-references, not this section's own synopsis.
        cmds = []
        for line in body:
            if line.strip() == "":
                continue
            if not line.startswith("    "):
                break  # reached prose; synopsis is over
            m = _SYNOPSIS_RE.match(line)
            if m:
                cmds.append(m.group(1))
        if not cmds:
            continue
        text = "\n".join([heading] + _strip_maintainer_lines(body)).strip("\n")
        for c in cmds:
            mapping.setdefault(c, text)
    return intro, mapping


def _parse_idea_help(path=_IDEA_MD):
    """The command -> section mapping (see `_parse_idea_sections`)."""
    return _parse_idea_sections(path)[1]


# name -> one-line description (also drives `dh_hl help`)
COMMAND_HELP = {
    "help": "List commands, or describe one command.",
    "status": "Report whether the workspace matches a tracked schedule node.",
    "restore_schedule": "Copy a schedule node's C++ into the workspace + set current idea.",
    "restore_idea": "Load an idea's parent schedule into the workspace to implement it.",
    "init_build": "Select up to 3 schedule nodes (target/other/anchor) for the next build.",
    "build": "Compile (and optionally profile) the init_build selection.",
    "copy_build_output": "Copy a build artifact (stmt/header/shared_library/...) to a file.",
    "canon": "Make the current schedule the canonical schedule of the current idea.",
    "comment": "Attach commentary (with a review and optional cancels) to a schedule node.",
    "new_root": "Create a new root schedule node from the workspace.",
    "set_idea": "Set the current idea state to an existing idea node.",
    "new_idea": "Add a child idea node (proposal) to a major schedule.",
    "list_child_ideas": "List the child idea nodes of a major schedule.",
    "list_seed_ideas": "List the current session's seed ideas.",
    "list_private_ideas": "Cost-ranked frontier of the session's private ideas by pool.",
    "init_workspace": "Initialize the session's private workspace to defaults.",
    "get_current_anchor": "Print the session's current anchor schedule.",
    "set_current_anchor": "Set (or clear) the session's current anchor schedule.",
    "get_halide_path": "Print the session's Halide directory path.",
    "set_halide_path": "Set the session's Halide directory path (needed to build).",
    "get_pool_tag": "Print a private idea's pool tag.",
    "set_pool_tag": "Set a private idea's pool tag (adding it to the list if needed).",
    "hide_private_idea": "Prepend '.' to a private idea's pool tag.",
    "rename_pool_tag": "Retag every private idea with a given pool tag.",
    "add_private_benchmark_set": "Add benchmark set(s) to the session's private list.",
    "remove_private_benchmark_set": "Remove benchmark set(s) from the private list.",
    "list_private_benchmark_sets": "List the session's private benchmark set IDs.",
    "list_output_schedules": "List the current session's output schedules.",
    "list_sibling_schedules": "List schedules sharing a parent idea with the given schedule.",
    "list_child_schedules": "List the child schedules of an idea node.",
    "list_equal_schedules": "List schedules with the same source hash as the given one.",
    "view_idea": "Show an idea node's proposal and child schedules.",
    "add_idea_side_link": "Add a borrows_from/superseded_by link between two idea nodes.",
    "force_parent_idea": "Parent a root schedule to an idea as its canonical (rare).",
    "view_generator_parameters": "Pretty-print a schedule node's generator parameters.",
    "json_schedule_info": "Dump a schedule node's full state as JSON.",
    "json_idea_info": "Dump an idea node's full state as JSON.",
    "json_benchmark_info": "Dump a benchmark sub-object as JSON.",
    "json_benchmark_set_info": "Dump a benchmark set as JSON.",
    "benchmark_full_id": "Print a benchmark's full ID (accepts a private short ID).",
    "json_ranking_cost": "Report a schedule's cost (with/without an anchor) as JSON.",
    "json_compare_cost": "Head-to-head 2-way cost comparison of two schedules as JSON.",
    "json_profiler_stats": "Aggregate profiler statistics for a schedule as JSON.",
    "view_benchmark_stdout": "Print the stdout captured for a benchmark.",
    "add_warning_toggle": "Add a WarningToggle (block a warning or cancel another) to a schedule.",
    "debug_warning_toggle": "List the WarningToggles in effect for a schedule node.",
    "view_benchmark_warnings": "Pretty-print a benchmark's profiler warnings (with block info).",
    "root_of": "Print the tree root of a schedule node.",
    "session_root_of": "Print the session-root schedule (child of a seed idea) above a node.",
    "history": "Walk from a schedule node up to its root, printing each hop.",
    "fix_canonical": "Resolve a canonical.txt merge conflict for an idea node.",
    "exec": "Run a command (after `--`) with the machine lock held (shared).",
    "exec_exclusive": "Run a command (after `--`) with the machine lock held exclusively.",
    "experiment": "Throwaway tools for the LLM Halide scheduling experiment.",
    # Phase 4: session lifecycle + queries
    "new_catalog": "Create a brand-new catalog + first session from an input C++ file.",
    "new_sub_session": "Spawn a sub-agent session (depth+1) off a parent schedule.",
    "new_successor_session": "Start a successor to a self-closed top-level session.",
    "should_accept": "Check a schedule's suitability as a primary output; print any close_session override flags.",
    "close_session": "Set the current session's output schedule (its final result).",
    "delist_session": "Mark the current session as delisted.",
    "join_session": "Merge another session's outputs into the current private lists.",
    "list_open_sessions": "List all open (not-closed) sessions with handles.",
    "list_termini": "List all termini (top-level, not-delisted, no successor).",
    "copy_schedule": "Write a schedule node's C++ (or params with --parameters) to a file ('-' for stdout).",
    "catalog_location": "Print the catalog directory path (resolves a session handle).",
    "workspace_schedule": "Print the path of the session's workspace C++ file.",
    "workspace_parameters": "Print the path of the session's workspace generator parameters file.",
    "workspace_bin": "Print the path of the session's bin directory.",
    "schedule_full_id": "Print a schedule node's full ID.",
    "schedule_short_id": "Print a schedule node's short ID.",
    "idea_full_id": "Print an idea node's full ID.",
    "idea_short_id": "Print an idea node's short ID.",
    "session_full_id": "Print the current session's full ID.",
    "session_handle": "Print (allocating if needed) the current session's handle.",
    "view_session_prompt": "Show the current session's prompt and seed ideas.",
    "view_commentary": "Show a single commentary sub-object by ID.",
    "view_all_commentary": "Show all commentary of a schedule node.",
    "view_session_commentary": "Show all commentary of the current session's output schedule.",
    "json_session_info": "Dump the current session's state as JSON.",
    "json_export": "Dump the entire catalog (ideas, schedules, sessions) as JSON.",
    # Golden objects
    "new_golden": "Create a golden object (remarks + optional schedule node).",
    "golden_history": "List golden objects, most recent first.",
    "json_golden_info": "Dump a golden object as JSON.",
    # Problem objects
    "new_problem": "Create a problem (runner command line) with a short name.",
    "disable_problem": "Set a problem's state to disabled.",
    "enable_problem": "Set a problem's state to enabled (leaves a main problem main).",
    "set_main_problem": "Make a problem the main problem (demoting any other main).",
    "get_problem_short_name": "Print a problem's short name.",
    "set_problem_short_name": "Set a problem's short name.",
    "list_enabled_problems": "List the enabled (incl. main) problems.",
    "list_all_problems": "List all problems.",
    "json_problem_info": "Dump a problem object as JSON.",
    "problem_full_id": "Print a problem's full ID.",
    "problem_short_id": "Print a problem's short ID.",
    "commentary_full_id": "Print a commentary sub-object's full ID.",
    "commentary_short_id": "Print a commentary sub-object's short ID.",
    "warning_toggle_full_id": "Print a WarningToggle's full ID.",
    "warning_toggle_short_id": "Print a WarningToggle's short ID.",
    "prompt": "Print the assembled main-agent or sub-agent prompt.",
    "detail": "Print a supplemental document from the harness `detail/` dir.",
    "examples": "Print an example file from the harness `examples/` dir.",
}


def get_command_help_dict():
    result = dict(COMMAND_HELP)
    if not guide_flag.enabled:
        del result["detail"]
        del result["examples"]
    return result


def _build_parser():
    p = argparse.ArgumentParser(prog="dh_hl", description="Dendritic Halide Harness")
    sub = p.add_subparsers(dest="command", metavar="command")

    def add(name):
        if not allow_harness_flag.enabled and name not in _NO_HARNESS_ALLOWLIST:
            # No-harness run: this tool is turned off, so it is never registered
            # (hidden from --help too).  A direct attempt is caught by the
            # pre-parse gate in main() with a clear message.  Return a detached
            # throwaway parser so the caller's .add_argument() calls are harmless.
            return argparse.ArgumentParser(add_help=False)
        sp = sub.add_parser(name, help=COMMAND_HELP[name])
        # Every tool accepts both -C and -s (idea.md); required-ness is enforced
        # per-tool via Context.for_catalog / for_session.
        sp.add_argument("-C", "--catalog", help="catalog directory (ends .dh_hl)")
        sp.add_argument("-s", "--session", help="session handle or full ID")
        return sp

    if allow_harness_flag.enabled or "help" in _NO_HARNESS_ALLOWLIST:
        hp = sub.add_parser("help", help=COMMAND_HELP["help"])
        hp.add_argument("topic", nargs="?", help="command to describe")

    add("status")

    sp = add("restore_schedule")
    sp.add_argument("schedule", help="schedule ID")

    sp = add("restore_idea")
    sp.add_argument("idea", help="idea ID")

    sp = add("init_build")
    # Target accepts a bare positional ID or --target (see build._init_build_target_spec);
    # --target has no argparse default so we can tell "given" from "omitted".
    sp.add_argument("target_pos", nargs="?", metavar="target",
                    help="target schedule ID (positional alias for --target)")
    sp.add_argument("--target",
                    help="target schedule ID, or 'workspace' (default); "
                         "alias for the positional target")
    sp.add_argument("--other", default="parent",
                    help="other schedule ID, 'parent' (default), or 'none'")
    sp.add_argument("--anchor", default="auto",
                    help="anchor schedule ID, 'auto' (default), 'always', or 'none'")

    sp = add("build")
    sp.add_argument("--profile", nargs="?", type=int, const=1, default=0,
                    metavar="N", help="profiler batches to run (default 0)")
    sp.add_argument("--only", default="all", metavar="N|target|all",
                    help="limit built binaries: 'all' (default), 'target', or index N")
    sp.add_argument("--problem", action="append", metavar="PROBLEM_ID",
                    help="profile with this problem (repeatable; default: all "
                         "enabled problems)")
    sp.add_argument("--gen-timeout", type=float, default=None, metavar="SECONDS",
                    help="kill any single Halide generator emit that runs longer "
                         "than SECONDS (fractional ok; SIGTERM then SIGKILL) and "
                         "fail the build. Applies ONLY to the generator step -- "
                         "NOT the C++/ninja "
                         "compile, which cannot be reliably killed without process "
                         "groups (build is not time-bounded in general)")
    sp.add_argument("--exec-timeout", type=float, default=None,
                    metavar="SECONDS",
                    help="kill any single profiler pipeline execution that runs "
                         "longer than SECONDS (fractional ok; SIGTERM then "
                         "SIGKILL) and fail that run. Applies ONLY to the "
                         "pipeline execution step")

    sp = add("copy_build_output")
    sp.add_argument("output", help="output file ('-' for stdout)")
    sp.add_argument("what", choices=build_mod.COPY_BUILD_WHATS,
                    help="which build artifact to copy")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")
    sp.add_argument("--parameters", type=int, metavar="N",
                    help="generator parameters index (required if the node has "
                         ">1 and 'what' is not 'generator')")

    add("canon")

    sp = add("comment")
    sp.add_argument("commentary", help="commentary file ('-' for stdin)")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")
    sp.add_argument("--review", default="neutral",
                    help="review value: neutral (default), negative, positive, "
                         "or lost_interest (not 'mixed')")
    sp.add_argument("--cancels", action="append", metavar="COMMENTARY_ID",
                    help="cancel a same-node commentary (repeatable)")

    add("new_root")

    sp = add("set_idea")
    sp.add_argument("idea", help="idea ID")

    sp = add("new_idea")
    sp.add_argument("proposal_name", help="proposal name [A-Za-z0-9_]{1,72}")
    sp.add_argument("proposal", help="proposal text file ('-' for stdin)")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")
    sp.add_argument("--pool-tag", dest="pool_tag",
                    help="pool tag (default: inherit the parent idea's)")

    sp = add("list_child_ideas")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    add("list_seed_ideas")

    sp = add("list_private_ideas")
    sp.add_argument("--anchor", default="auto",
                    help="anchor schedule ID, 'auto' (default), 'always', or 'none'")
    sp.add_argument("--problem", metavar="PROBLEM_ID",
                    help="problem for the cost model (default: main problem)")
    sp.add_argument("--confidence", type=float, metavar="CI",
                    help="confidence for obsoleted-by CI, 0 < ci < 1 (default 0.95)")
    sp.add_argument("--max", type=int, metavar="N",
                    help="list up to N ideas per pool tag (default 6)")
    sp.add_argument("--pool", action="append", metavar="NAME",
                    help="enable a pool tag by exact name (repeatable)")
    sp.add_argument("--pools", action="append", metavar="REGEX",
                    help="enable pool tags matching a regex (repeatable)")
    grp = sp.add_mutually_exclusive_group()
    grp.add_argument("--done", action="store_true",
                     help="only ideas with a canonical schedule")
    grp.add_argument("--todo", action="store_true",
                     help="only ideas without a canonical schedule")

    sp = add("init_workspace")
    sp.add_argument("--force", action="store_true",
                    help="reinitialize even if workspace state already exists")

    add("get_current_anchor")

    sp = add("set_current_anchor")
    sp.add_argument("schedule", nargs="?",
                    help="schedule ID, or 'none' to clear (default: status)")

    add("get_halide_path")

    sp = add("set_halide_path")
    sp.add_argument("path", help="path to the Halide directory (has a build/ dir)")

    sp = add("get_pool_tag")
    sp.add_argument("idea", help="idea ID")

    sp = add("set_pool_tag")
    sp.add_argument("idea", help="idea ID")
    sp.add_argument("pool_tag", help="pool tag to assign")

    sp = add("hide_private_idea")
    sp.add_argument("idea", help="idea ID")

    sp = add("rename_pool_tag")
    sp.add_argument("pool_tag_before", help="existing pool tag")
    sp.add_argument("pool_tag_after", help="new pool tag")

    sp = add("add_private_benchmark_set")
    sp.add_argument("benchmark_sets", nargs="*", help="benchmark set full IDs")

    sp = add("remove_private_benchmark_set")
    sp.add_argument("benchmark_sets", nargs="*", help="benchmark set full IDs")

    add("list_private_benchmark_sets")

    add("list_output_schedules")

    sp = add("list_sibling_schedules")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("list_child_schedules")
    sp.add_argument("idea", help="idea ID")

    sp = add("list_equal_schedules")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("view_idea")
    sp.add_argument("idea", help="idea ID")

    sp = add("add_idea_side_link")
    sp.add_argument("idea_lhs", help="source idea ID")
    sp.add_argument("type", help="borrows_from or superseded_by")
    sp.add_argument("idea_rhs", help="destination idea ID")

    sp = add("force_parent_idea")
    sp.add_argument("idea", help="idea ID")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("view_generator_parameters")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("json_schedule_info")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("json_idea_info")
    sp.add_argument("idea", help="idea ID")

    sp = add("json_benchmark_info")
    sp.add_argument("benchmark", help="benchmark ID")

    sp = add("benchmark_full_id")
    sp.add_argument("benchmark", help="benchmark ID (full or private short ID)")

    sp = add("json_benchmark_set_info")
    sp.add_argument("benchmark_set", help="benchmark set ID")

    sp = add("json_ranking_cost")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")
    sp.add_argument("--anchor", default="auto",
                    help="anchor schedule ID, 'auto' (default), 'always', or 'none'")
    sp.add_argument("--problem", metavar="PROBLEM_ID",
                    help="problem for the cost model (default: main problem)")

    sp = add("json_compare_cost")
    sp.add_argument("lhs", nargs="?", help="LHS schedule ID (default: status)")
    sp.add_argument("rhs", nargs="?",
                    help="RHS schedule ID (default: parent of LHS's parent idea)")
    sp.add_argument("--confidence", type=float, metavar="CI",
                    help="confidence for the CI, 0 < ci < 1 (default 0.95)")
    sp.add_argument("--bootstrap", type=int, metavar="B",
                    help="bootstrap resample count (default: shared frontier B)")
    sp.add_argument("--problem", action="append", metavar="PROBLEM_ID",
                    help="compare for this problem (repeatable; default: all "
                         "enabled problems)")
    sp.add_argument("--boolean", action="store_true",
                    help="collapse to {any_improvement, any_regression, any_unknown}")

    sp = add("json_profiler_stats")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")
    sp.add_argument("--problem", metavar="PROBLEM_ID",
                    help="problem for the stats (default: main problem)")
    sp.add_argument("-f", action="append", metavar="NAME",
                    help="include a per-function statistic (repeatable)")
    sp.add_argument("-p", action="append", metavar="NAME",
                    help="include a pipeline-global statistic (repeatable)")
    sp.add_argument("--parameters", type=int, metavar="N",
                    help="restrict to the N-th generator parameters object")
    sp.add_argument("--hottest", type=int, metavar="N",
                    help="output only the N hottest functions")

    sp = add("view_benchmark_stdout")
    sp.add_argument("benchmark", help="benchmark ID")

    sp = add("add_warning_toggle")
    sp.add_argument("schedule", help="schedule ID")
    sp.add_argument("commentary", help="commentary ID to cite")
    sp.add_argument("--block", nargs=2, metavar=("RULE", "FUNC"),
                    help="block warnings with this (rule, func) pair")
    sp.add_argument("--cancel", metavar="WARNING_TOGGLE_ID",
                    help="cancel (re-enable) another WarningToggle")

    sp = add("debug_warning_toggle")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")
    sp.add_argument("--block", nargs=2, metavar=("RULE", "FUNC"),
                    help="only toggles blocking this (rule, func) pair")
    sp.add_argument("--cancel", metavar="WARNING_TOGGLE_ID",
                    help="only toggles that cancel this WarningToggle")

    sp = add("view_benchmark_warnings")
    sp.add_argument("benchmark", help="benchmark ID")
    sp.add_argument("--always-show-message", action="store_true",
                    help="print the message even for blocked warnings")

    sp = add("history")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("root_of")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("session_root_of")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("fix_canonical")
    sp.add_argument("idea", help="idea ID")

    # -- Phase 4: session lifecycle + queries --------------------------------
    sp = add("new_catalog")
    sp.add_argument("proposal_name", help="seed idea proposal name [A-Za-z0-9_]{1,72}")
    sp.add_argument("proposal", help="prompt file ('-' for stdin)")
    sp.add_argument("input_cpp", help="initial C++ generator file ('-' for stdin)")
    sp.add_argument("input_parameters", nargs="?",
                    help="generator parameters JSON list file ('-' for stdin; "
                         "default [{}])")

    sp = add("new_sub_session")
    sp.add_argument("proposal_name", help="proposal name [A-Za-z0-9_]{1,72}")
    sp.add_argument("proposal", help="prompt file ('-' for stdin)")
    sp.add_argument("schedule", nargs="*",
                    help="parent schedule IDs (default: status)")

    sp = add("new_successor_session")
    sp.add_argument("proposal_name", help="proposal name [A-Za-z0-9_]{1,72}")
    sp.add_argument("proposal", help="prompt file ('-' for stdin)")

    sp = add("should_accept")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("close_session")
    sp.add_argument("schedule", nargs="*",
                    help="output schedule IDs (default: status)")
    # should_accept override flags (idea.md "Close Session Tool").
    sp.add_argument("--allow-failed-problems", action="store_true",
                    help="force close despite a failed problem check")
    sp.add_argument("--allow-failed-golden", action="store_true",
                    help="force close despite a failed golden check")
    sp.add_argument("--allow-disabled-problems", action="store_true",
                    help="force close despite an enabled-on-opening problem "
                         "now being disabled")
    sp.add_argument("--allow-changed-golden", action="store_true",
                    help="force close despite the golden schedule node changing")

    add("delist_session")

    sp = add("join_session")
    sp.add_argument("joined", help="the joined session (handle or full ID)")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="print what would be joined, mutating nothing")
    sp.add_argument("--pool-prefix", dest="pool_prefix", default="",
                    help="prefix for newly-added ideas' pool tags")

    for name in ("list_open_sessions", "list_termini"):
        sp = add(name)
        sp.add_argument("--json", action="store_true",
                        help="output a JSON list of session full IDs (no handles)")

    sp = add("copy_schedule")
    sp.add_argument("output", help="output file ('-' for stdout)")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")
    sp.add_argument("--parameters", action="store_true",
                    help="copy generator_parameters.json instead of the C++")

    add("catalog_location")

    add("workspace_schedule")
    add("workspace_parameters")
    add("workspace_bin")

    sp = add("schedule_full_id")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")
    sp = add("schedule_short_id")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("idea_full_id")
    sp.add_argument("idea", help="idea ID")
    sp = add("idea_short_id")
    sp.add_argument("idea", help="idea ID")

    add("session_full_id")
    add("session_handle")
    add("view_session_prompt")

    sp = add("view_commentary")
    sp.add_argument("commentary", help="commentary ID")
    sp.add_argument("--brief", action="store_true",
                    help="print only the first line (up to 72 chars)")

    sp = add("view_all_commentary")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")
    sp.add_argument("--brief", action="store_true",
                    help="print only the first line (up to 72 chars) of each")

    sp = add("view_session_commentary")
    sp.add_argument("--brief", action="store_true",
                    help="print only the first line (up to 72 chars) of each")
    add("json_session_info")
    add("json_export")

    # -- Golden objects ------------------------------------------------------
    sp = add("new_golden")
    sp.add_argument("remarks", help="remarks file ('-' for stdin)")
    sp.add_argument("schedule", nargs="?",
                    help="schedule ID, or 'none' for no schedule (default: status)")

    add("golden_history")

    sp = add("json_golden_info")
    sp.add_argument("golden", help="golden object ID")

    # -- Problem objects -----------------------------------------------------
    sp = add("new_problem")
    sp.add_argument("short_name", help="short name [A-Za-z0-9_]+")
    # argparse.REMAINDER captures the runner command line verbatim, including
    # tokens that look like flags (e.g. --benchmarks=all) or placeholders
    # (<RunGenMain>, <Lib>).  Put -C/-s BEFORE the short name.
    sp.add_argument("argv", nargs=argparse.REMAINDER,
                    help="runner command line (special: <RunGenMain>, <Lib>)")

    for name in ("disable_problem", "enable_problem", "set_main_problem",
                 "get_problem_short_name", "json_problem_info",
                 "problem_full_id", "problem_short_id"):
        sp = add(name)
        sp.add_argument("problem", help="problem ID")

    sp = add("set_problem_short_name")
    sp.add_argument("problem", help="problem ID")
    sp.add_argument("short_name", help="new short name [A-Za-z0-9_]+")

    for name in ("commentary_full_id", "commentary_short_id"):
        sp = add(name)
        sp.add_argument("commentary", help="commentary ID")
    for name in ("warning_toggle_full_id", "warning_toggle_short_id"):
        sp = add(name)
        sp.add_argument("warning_toggle", help="WarningToggle ID")

    add("list_enabled_problems")
    add("list_all_problems")

    sp = add("prompt")
    grp = sp.add_mutually_exclusive_group(required=True)
    grp.add_argument("--main", action="store_true",
                     help="emit the main-agent prompt")
    grp.add_argument("--sub", action="store_true",
                     help="emit the sub-agent prompt")
    # Undocumented (guide-ablation experiment): emit only the standalone guide
    # docs, with the harness_T blocks removed.  Suppressed from --help.
    grp.add_argument("--guide-only", dest="guide_only", action="store_true",
                     help=argparse.SUPPRESS)

    if guide_flag.enabled:
        sp = add("detail")
        sp.add_argument("name", help="file name inside the detail/ directory")

        sp = add("examples")
        sp.add_argument("name", help="file name inside the examples/ directory")

    # Throwaway experiment tools (registered even when the guide is disabled, so
    # the guide-ablation harness can drive it either way).  A single positional
    # action selects the sub-tool; the optional positionals carry the label
    # (begin), the two file paths (add_schedule_node), or the two file paths + a
    # bin directory + a Halide path (build_external, which needs no -C).
    sp = add("experiment")
    sp.add_argument("action", choices=["begin", "get_begin_label",
                                       "get_begin_timestamp", "time",
                                       "add_schedule_node",
                                       "json_test_schedules", "build_external"])
    sp.add_argument("arg1", nargs="?",
                    help="begin: label; add_schedule_node/build_external: "
                         "generator.cpp path")
    sp.add_argument("arg2", nargs="?",
                    help="add_schedule_node/build_external: "
                         "generator_parameters.json path")
    sp.add_argument("arg3", nargs="?",
                    help="build_external: output bin directory")
    sp.add_argument("arg4", nargs="?",
                    help="build_external: Halide path")
    sp.add_argument("--ignore", action="append", metavar="TEXT",
                    help="add_schedule_node: add an 'EXPERIMENT IGNORE: TEXT' "
                         "negative commentary (repeatable)")

    return p


_DISPATCH = {
    "status": tools.cmd_status,
    "restore_schedule": tools.cmd_restore_schedule,
    "restore_idea": tools.cmd_restore_idea,
    "init_build": build_mod.cmd_init_build,
    "build": build_mod.cmd_build,
    "copy_build_output": build_mod.cmd_copy_build_output,
    "canon": tools.cmd_canon,
    "comment": tools.cmd_comment,
    "new_root": tools.cmd_new_root,
    "set_idea": tools.cmd_set_idea,
    "new_idea": tools.cmd_new_idea,
    "list_child_ideas": tools.cmd_list_child_ideas,
    "list_seed_ideas": tools.cmd_list_seed_ideas,
    "list_private_ideas": tools.cmd_list_private_ideas,
    "init_workspace": tools.cmd_init_workspace,
    "get_current_anchor": tools.cmd_get_current_anchor,
    "set_current_anchor": tools.cmd_set_current_anchor,
    "get_halide_path": tools.cmd_get_halide_path,
    "set_halide_path": tools.cmd_set_halide_path,
    "get_pool_tag": tools.cmd_get_pool_tag,
    "set_pool_tag": tools.cmd_set_pool_tag,
    "hide_private_idea": tools.cmd_hide_private_idea,
    "rename_pool_tag": tools.cmd_rename_pool_tag,
    "add_private_benchmark_set": tools.cmd_add_private_benchmark_set,
    "remove_private_benchmark_set": tools.cmd_remove_private_benchmark_set,
    "list_private_benchmark_sets": tools.cmd_list_private_benchmark_sets,
    "list_output_schedules": tools.cmd_list_output_schedules,
    "list_sibling_schedules": tools.cmd_list_sibling_schedules,
    "list_child_schedules": tools.cmd_list_child_schedules,
    "list_equal_schedules": tools.cmd_list_equal_schedules,
    "view_idea": tools.cmd_view_idea,
    "add_idea_side_link": tools.cmd_add_idea_side_link,
    "force_parent_idea": tools.cmd_force_parent_idea,
    "view_generator_parameters": tools.cmd_view_generator_parameters,
    "json_schedule_info": tools.cmd_json_schedule_info,
    "json_idea_info": tools.cmd_json_idea_info,
    "json_benchmark_info": tools.cmd_json_benchmark_info,
    "json_benchmark_set_info": tools.cmd_json_benchmark_set_info,
    "benchmark_full_id": tools.cmd_benchmark_full_id,
    "json_ranking_cost": tools.cmd_json_ranking_cost,
    "json_compare_cost": tools.cmd_json_compare_cost,
    "json_profiler_stats": tools.cmd_json_profiler_stats,
    "view_benchmark_stdout": tools.cmd_view_benchmark_stdout,
    "add_warning_toggle": tools.cmd_add_warning_toggle,
    "debug_warning_toggle": tools.cmd_debug_warning_toggle,
    "view_benchmark_warnings": tools.cmd_view_benchmark_warnings,
    "root_of": tools.cmd_root_of,
    "session_root_of": tools.cmd_session_root_of,
    "history": tools.cmd_history,
    "fix_canonical": tools.cmd_fix_canonical,
    # Phase 4
    "new_catalog": tools.cmd_new_catalog,
    "new_sub_session": tools.cmd_new_sub_session,
    "new_successor_session": tools.cmd_new_successor_session,
    "should_accept": tools.cmd_should_accept,
    "close_session": tools.cmd_close_session,
    "delist_session": tools.cmd_delist_session,
    "join_session": tools.cmd_join_session,
    "list_open_sessions": tools.cmd_list_open_sessions,
    "list_termini": tools.cmd_list_termini,
    "copy_schedule": tools.cmd_copy_schedule,
    "catalog_location": tools.cmd_catalog_location,
    "workspace_schedule": tools.cmd_workspace_schedule,
    "workspace_parameters": tools.cmd_workspace_parameters,
    "workspace_bin": tools.cmd_workspace_bin,
    "schedule_full_id": tools.cmd_schedule_full_id,
    "schedule_short_id": tools.cmd_schedule_short_id,
    "idea_full_id": tools.cmd_idea_full_id,
    "idea_short_id": tools.cmd_idea_short_id,
    "session_full_id": tools.cmd_session_full_id,
    "session_handle": tools.cmd_session_handle,
    "view_session_prompt": tools.cmd_view_session_prompt,
    "view_commentary": tools.cmd_view_commentary,
    "view_all_commentary": tools.cmd_view_all_commentary,
    "view_session_commentary": tools.cmd_view_session_commentary,
    "json_session_info": tools.cmd_json_session_info,
    "json_export": tools.cmd_json_export,
    "new_golden": tools.cmd_new_golden,
    "golden_history": tools.cmd_golden_history,
    "json_golden_info": tools.cmd_json_golden_info,
    "new_problem": tools.cmd_new_problem,
    "disable_problem": tools.cmd_disable_problem,
    "enable_problem": tools.cmd_enable_problem,
    "set_main_problem": tools.cmd_set_main_problem,
    "get_problem_short_name": tools.cmd_get_problem_short_name,
    "set_problem_short_name": tools.cmd_set_problem_short_name,
    "list_enabled_problems": tools.cmd_list_enabled_problems,
    "list_all_problems": tools.cmd_list_all_problems,
    "json_problem_info": tools.cmd_json_problem_info,
    "problem_full_id": tools.cmd_problem_full_id,
    "problem_short_id": tools.cmd_problem_short_id,
    "commentary_full_id": tools.cmd_commentary_full_id,
    "commentary_short_id": tools.cmd_commentary_short_id,
    "warning_toggle_full_id": tools.cmd_warning_toggle_full_id,
    "warning_toggle_short_id": tools.cmd_warning_toggle_short_id,
    "prompt": tools.cmd_prompt,
    "detail": tools.cmd_detail,
    "examples": tools.cmd_examples,
    "experiment": tools.cmd_experiment,
}


def cmd_help(args):
    cmd_dict = get_command_help_dict()
    if args.topic is None:
        print("dh_hl commands:\n")
        for name in cmd_dict:
            print("  {:20} {}".format(name, cmd_dict[name]))
        intro, _ = _parse_idea_sections()
        if intro:
            print("\n" + intro)
        print("\nUse `dh_hl help <command>` or `dh_hl <command> -h` for details.")
        return
    if args.topic not in cmd_dict:
        raise DhHlError("no such command: " + args.topic)
    # Detailed help: the idea.md tool section, if available; else the one-liner.
    section = _parse_idea_help().get(args.topic)
    if section is not None:
        print(section)
    else:
        print("{}: {}".format(args.topic, cmd_dict[args.topic]))


def _cmd_exec(kind, rest):
    """Run a command with the machine lock held.  The machine lock is already
    held (shared); `exec_exclusive` upgrades it.  Everything after the first
    `--` is the command argv; any `-C`/`-s` before it is accepted but currently
    unused (the machine lock needs neither catalog nor session)."""
    if "--" not in rest:
        raise DhHlError(
            "{0} requires `--` followed by the command to run, e.g. "
            "`dh_hl {0} -- cat file.txt`".format(kind))
    command = rest[rest.index("--") + 1:]
    if not command:
        raise DhHlError("no command given after `--`")
    if kind == "exec_exclusive":
        locks.upgrade_machine_exclusive()
    sys.exit(subprocess.call(command))


def main():
    safety.arm()
    # Machine lock first thing, before any other work, to yield the machine to
    # any in-progress profiling (which holds it exclusively) as soon as possible.
    locks.acquire_machine_shared()
    argv = sys.argv[1:]
    try:
        # DRM (no-harness experiment run): only the allowlist is available.  Catch
        # a real-but-turned-off command up front with a clear, imperative message
        # (an unknown name falls through to argparse's usage against the allowed
        # set).  This precedes the exec/init_build shortcuts below, so those are
        # gated too.
        if not allow_harness_flag.enabled and argv and argv[0] in COMMAND_HELP \
                and argv[0] not in _NO_HARNESS_ALLOWLIST:
            sys.stderr.write(
                "dh_hl: the '{}' tool is turned OFF for this run and cannot be "
                "used. Work with the plain files and tools you were given; do "
                "not attempt to re-enable the harness.\n".format(argv[0]))
            sys.exit(2)
        if argv and argv[0] in ("exec", "exec_exclusive"):
            _cmd_exec(argv[0], argv[1:])
            return
        if argv and argv[0] == "init_build":
            # Clear any stale selection BEFORE the strict parse, so an init_build
            # that argparse rejects still can't leave an earlier success's
            # selection for `build` to reuse (idea.md Init-Build Tool footgun).
            build_mod.invalidate_selection_best_effort(argv[1:])
        parser = _build_parser()
        args = parser.parse_args()
        if args.command is None:
            parser.print_help()
            sys.exit(2)
        if args.command == "help":
            cmd_help(args)
        else:
            _DISPATCH[args.command](args)
    except DhHlError as e:
        print("dh_hl: " + str(e), file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:
        sys.exit(0)
