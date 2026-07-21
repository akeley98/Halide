"""dh_hl command-line entry point: argparse dispatch + help tool."""

import argparse
import os
import re
import subprocess
import sys

from . import locks
from . import safety
from . import tools
from . import build as build_mod
from .errors import DhHlError

# idea.md is the human-facing spec; `dh_hl help <command>` renders the relevant
# tool section from it so the detailed per-command docs have a single source.
# It sits one level above the package dir (dendritic_hl/idea.md); it is outside
# the package, so a copy run detached from the repo won't find it -- callers
# fall back to the COMMAND_HELP one-liner in that case.
_IDEA_MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "idea.md")

# A synopsis line inside a tool section, e.g. "    dh_hl status -s ..." (possibly
# after a leading "# comment").  The command name is what we key sections by.
_SYNOPSIS_RE = re.compile(r"^    (?:# .*)?dh_hl ([a-z_]+)\b")


def _strip_maintainer_lines(lines):
    """Drop lines that are for maintainers, not `dh_hl help` readers."""
    return [ln for ln in lines
            if not ln.strip().startswith("NOTE: [link") and "<!--" not in ln]


def _parse_idea_sections(path=_IDEA_MD):
    """Parse the idea.md "## Tools" section.  Returns `(intro, mapping)`:

    * `intro` — the prose between the "## Tools" heading and the first "###"
      tool section (the common usage notes shown by `dh_hl help` with no arg).
    * `mapping` — command name -> the text of the "### ... Tool" section that
      documents it, keyed by the commands in each section's *leading* indented
      synopsis block (so a multi-command section maps all its commands to the
      same shared text).

    Maintainer-only lines are stripped from both.  Returns `("", {})` if idea.md
    can't be read.  See the FORMAT CONTRACT comment above "## Tools" in idea.md."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        return "", {}

    intro_lines = None       # collecting the "## Tools" intro (until first "###")
    intro_captured = []      # the finished intro (saved at that first "###")
    sections = []            # list of (heading, body_lines)
    cur = None
    for line in lines:
        if line.startswith("### "):
            if intro_lines is not None:
                intro_captured = intro_lines  # intro ends at the first tool section
            intro_lines = None
            cur = (line, [])
            sections.append(cur)
        elif line.startswith("## "):
            cur = None
            intro_lines = [] if line.strip() == "## Tools" else None
        elif intro_lines is not None:
            intro_lines.append(line)
        elif cur is not None:
            cur[1].append(line)
    if intro_lines is not None:  # "## Tools" had no following "###" (degenerate)
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
    "build": "Compile the workspace and add/update its schedule node.",
    "profile": "Like build, but benchmark with the profiler over parameter sets.",
    "canon": "Make the current schedule the canonical schedule of the current idea.",
    "comment": "Attach commentary text to a schedule node.",
    "comment_importance": "Attach commentary with an integer importance value.",
    "new_root": "Create a new root schedule node from the workspace.",
    "set_idea": "Set the current idea state to an existing idea node.",
    "new_idea": "Add a child idea node (proposal) to a major schedule.",
    "list_ideas": "List the child idea nodes of a major schedule.",
    "list_private_ideas": "List the current session's private idea list.",
    "list_private_ideas_todo": "List private ideas without a canonical schedule.",
    "list_private_ideas_done": "List private ideas with a canonical schedule.",
    "forget_private_idea": "Remove an idea from the session's private idea list.",
    "list_sibling_schedules": "List schedules sharing a parent idea with the given schedule.",
    "list_child_schedules": "List the child schedules of an idea node.",
    "list_equal_schedules": "List schedules with the same source hash as the given one.",
    "view_idea": "Show an idea node's proposal and child schedules.",
    "force_parent_idea": "Parent a root schedule to an idea as its canonical (rare).",
    "json_schedule_info": "Dump a schedule node's full state as JSON.",
    "json_idea_info": "Dump an idea node's full state as JSON.",
    "history": "Walk from a schedule node up to its root, printing each hop.",
    "fix_canonical": "Resolve a canonical.txt merge conflict for an idea node.",
    "exec": "Run a command (after `--`) with the machine lock held (shared).",
    "exec_exclusive": "Run a command (after `--`) with the machine lock held exclusively.",
    # Phase 4: session lifecycle + queries
    "new_catalog": "Create a brand-new catalog + first session from an input C++ file.",
    "new_sub_session": "Spawn a sub-agent session (depth+1) off a parent schedule.",
    "new_successor_session": "Start a successor to a self-closed top-level session.",
    "close_session": "Set the current session's output schedule (its final result).",
    "delist_session": "Mark the current session as delisted.",
    "list_open_sessions": "List all open (not-closed) sessions with handles.",
    "list_termini": "List all termini (top-level, not-delisted, no successor).",
    "copy_schedule": "Write a schedule node's C++ to a file ('-' for stdout).",
    "copy_terminus_schedule": "Write the unique terminus's output schedule C++ to a file.",
    "copy_seed_schedule": "Write the session seed idea's canonical C++ to a file.",
    "copy_session_output": "Write the current session's output schedule C++ to a file.",
    "catalog_location": "Print the catalog directory path (resolves a session handle).",
    "terminus_schedule_full_id": "Print the terminus output schedule's full ID.",
    "terminus_schedule_short_id": "Print the terminus output schedule's short ID.",
    "seed_schedule_full_id": "Print the session seed idea's canonical schedule full ID.",
    "seed_schedule_short_id": "Print the session seed idea's canonical schedule short ID.",
    "session_output_full_id": "Print the current session's output schedule full ID.",
    "session_output_short_id": "Print the current session's output schedule short ID.",
    "workspace_schedule": "Print the path of the session's workspace C++ file.",
    "workspace_bin": "Print the path of the session's bin directory.",
    "schedule_full_id": "Print a schedule node's full ID.",
    "schedule_short_id": "Print a schedule node's short ID.",
    "idea_full_id": "Print an idea node's full ID.",
    "idea_short_id": "Print an idea node's short ID.",
    "session_full_id": "Print the current session's full ID.",
    "session_handle": "Print (allocating if needed) the current session's handle.",
    "view_session_idea": "Show the current session's seed idea.",
    "view_commentary": "Show all commentary of a schedule node.",
    "view_session_commentary": "Show the session output's positive-importance commentary.",
    "json_session_info": "Dump the current session's state as JSON.",
    "json_export": "Dump the entire catalog (ideas, schedules, sessions) as JSON.",
    "prompt": "Print the assembled main-agent or sub-agent prompt.",
}


def _build_parser():
    p = argparse.ArgumentParser(prog="dh_hl", description="Dendritic Halide Harness")
    sub = p.add_subparsers(dest="command", metavar="command")

    def add(name):
        sp = sub.add_parser(name, help=COMMAND_HELP[name])
        # Every tool accepts both -C and -s (idea.md); required-ness is enforced
        # per-tool via Context.for_catalog / for_session.
        sp.add_argument("-C", "--catalog", help="catalog directory (ends .dh_hl)")
        sp.add_argument("-s", "--session", help="session handle or full ID")
        return sp

    hp = sub.add_parser("help", help=COMMAND_HELP["help"])
    hp.add_argument("topic", nargs="?", help="command to describe")

    add("status")

    sp = add("restore_schedule")
    sp.add_argument("schedule", help="schedule ID")

    sp = add("restore_idea")
    sp.add_argument("idea", help="idea ID")

    sp = add("build")
    sp.add_argument("parameters", nargs="?",
                    help="generator parameters JSON file ('-' for stdin)")

    sp = add("profile")
    sp.add_argument("parameters", nargs="?",
                    help="generator parameters JSON file ('-' for stdin)")

    add("canon")

    sp = add("comment")
    sp.add_argument("commentary", help="commentary file ('-' for stdin)")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("comment_importance")
    sp.add_argument("commentary", help="commentary file ('-' for stdin)")
    sp.add_argument("importance", type=int, help="integer importance value")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    add("new_root")

    sp = add("set_idea")
    sp.add_argument("idea", help="idea ID")

    sp = add("new_idea")
    sp.add_argument("proposal_name", help="proposal name [A-Za-z0-9_]{1,72}")
    sp.add_argument("proposal", help="proposal text file ('-' for stdin)")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("list_ideas")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    for _name in ("list_private_ideas", "list_private_ideas_todo",
                  "list_private_ideas_done"):
        sp = add(_name)
        sp.add_argument("n", nargs="?", type=int,
                        help="list only the first up-to-N ideas")

    sp = add("forget_private_idea")
    sp.add_argument("idea", help="idea ID")

    sp = add("list_sibling_schedules")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("list_child_schedules")
    sp.add_argument("idea", help="idea ID")

    sp = add("list_equal_schedules")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("view_idea")
    sp.add_argument("idea", help="idea ID")

    sp = add("force_parent_idea")
    sp.add_argument("idea", help="idea ID")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("json_schedule_info")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("json_idea_info")
    sp.add_argument("idea", help="idea ID")

    sp = add("history")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("fix_canonical")
    sp.add_argument("idea", help="idea ID")

    # -- Phase 4: session lifecycle + queries --------------------------------
    sp = add("new_catalog")
    sp.add_argument("proposal_name", help="seed idea proposal name [A-Za-z0-9_]{1,72}")
    sp.add_argument("proposal", help="seed idea proposal text file ('-' for stdin)")
    sp.add_argument("input_cpp", help="initial C++ generator file ('-' for stdin)")

    sp = add("new_sub_session")
    sp.add_argument("proposal_name", help="proposal name [A-Za-z0-9_]{1,72}")
    sp.add_argument("proposal", help="proposal text file ('-' for stdin)")
    sp.add_argument("schedule", nargs="?", help="parent schedule ID (default: status)")

    sp = add("new_successor_session")
    sp.add_argument("proposal_name", help="proposal name [A-Za-z0-9_]{1,72}")
    sp.add_argument("proposal", help="proposal text file ('-' for stdin)")

    sp = add("close_session")
    sp.add_argument("schedule", nargs="?", help="output schedule ID (default: status)")

    add("delist_session")
    add("list_open_sessions")
    add("list_termini")

    sp = add("copy_schedule")
    sp.add_argument("output", help="output file ('-' for stdout)")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    sp = add("copy_terminus_schedule")
    sp.add_argument("output", help="output file ('-' for stdout)")

    sp = add("copy_seed_schedule")
    sp.add_argument("output", help="output file ('-' for stdout)")

    sp = add("copy_session_output")
    sp.add_argument("output", help="output file ('-' for stdout)")

    add("catalog_location")

    add("terminus_schedule_full_id")
    add("terminus_schedule_short_id")
    add("seed_schedule_full_id")
    add("seed_schedule_short_id")
    add("session_output_full_id")
    add("session_output_short_id")
    add("workspace_schedule")
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
    add("view_session_idea")

    sp = add("view_commentary")
    sp.add_argument("schedule", nargs="?", help="schedule ID (default: status)")

    add("view_session_commentary")
    add("json_session_info")
    add("json_export")

    sp = add("prompt")
    grp = sp.add_mutually_exclusive_group(required=True)
    grp.add_argument("--main", action="store_true",
                     help="emit the main-agent prompt")
    grp.add_argument("--sub", action="store_true",
                     help="emit the sub-agent prompt")

    return p


_DISPATCH = {
    "status": tools.cmd_status,
    "restore_schedule": tools.cmd_restore_schedule,
    "restore_idea": tools.cmd_restore_idea,
    "build": build_mod.cmd_build,
    "profile": build_mod.cmd_profile,
    "canon": tools.cmd_canon,
    "comment": tools.cmd_comment,
    "comment_importance": tools.cmd_comment_importance,
    "new_root": tools.cmd_new_root,
    "set_idea": tools.cmd_set_idea,
    "new_idea": tools.cmd_new_idea,
    "list_ideas": tools.cmd_list_ideas,
    "list_private_ideas": tools.cmd_list_private_ideas,
    "list_private_ideas_todo": tools.cmd_list_private_ideas_todo,
    "list_private_ideas_done": tools.cmd_list_private_ideas_done,
    "forget_private_idea": tools.cmd_forget_private_idea,
    "list_sibling_schedules": tools.cmd_list_sibling_schedules,
    "list_child_schedules": tools.cmd_list_child_schedules,
    "list_equal_schedules": tools.cmd_list_equal_schedules,
    "view_idea": tools.cmd_view_idea,
    "force_parent_idea": tools.cmd_force_parent_idea,
    "json_schedule_info": tools.cmd_json_schedule_info,
    "json_idea_info": tools.cmd_json_idea_info,
    "history": tools.cmd_history,
    "fix_canonical": tools.cmd_fix_canonical,
    # Phase 4
    "new_catalog": tools.cmd_new_catalog,
    "new_sub_session": tools.cmd_new_sub_session,
    "new_successor_session": tools.cmd_new_successor_session,
    "close_session": tools.cmd_close_session,
    "delist_session": tools.cmd_delist_session,
    "list_open_sessions": tools.cmd_list_open_sessions,
    "list_termini": tools.cmd_list_termini,
    "copy_schedule": tools.cmd_copy_schedule,
    "copy_terminus_schedule": tools.cmd_copy_terminus_schedule,
    "copy_seed_schedule": tools.cmd_copy_seed_schedule,
    "copy_session_output": tools.cmd_copy_session_output,
    "catalog_location": tools.cmd_catalog_location,
    "terminus_schedule_full_id": tools.cmd_terminus_schedule_full_id,
    "terminus_schedule_short_id": tools.cmd_terminus_schedule_short_id,
    "seed_schedule_full_id": tools.cmd_seed_schedule_full_id,
    "seed_schedule_short_id": tools.cmd_seed_schedule_short_id,
    "session_output_full_id": tools.cmd_session_output_full_id,
    "session_output_short_id": tools.cmd_session_output_short_id,
    "workspace_schedule": tools.cmd_workspace_schedule,
    "workspace_bin": tools.cmd_workspace_bin,
    "schedule_full_id": tools.cmd_schedule_full_id,
    "schedule_short_id": tools.cmd_schedule_short_id,
    "idea_full_id": tools.cmd_idea_full_id,
    "idea_short_id": tools.cmd_idea_short_id,
    "session_full_id": tools.cmd_session_full_id,
    "session_handle": tools.cmd_session_handle,
    "view_session_idea": tools.cmd_view_session_idea,
    "view_commentary": tools.cmd_view_commentary,
    "view_session_commentary": tools.cmd_view_session_commentary,
    "json_session_info": tools.cmd_json_session_info,
    "json_export": tools.cmd_json_export,
    "prompt": tools.cmd_prompt,
}


def cmd_help(args):
    if args.topic is None:
        print("dh_hl commands:\n")
        for name in COMMAND_HELP:
            print("  {:20} {}".format(name, COMMAND_HELP[name]))
        intro, _ = _parse_idea_sections()
        if intro:
            print("\n" + intro)
        print("\nUse `dh_hl help <command>` or `dh_hl <command> -h` for details.")
        return
    if args.topic not in COMMAND_HELP:
        raise DhHlError("no such command: " + args.topic)
    # Detailed help: the idea.md tool section, if available; else the one-liner.
    section = _parse_idea_help().get(args.topic)
    if section is not None:
        print(section)
    else:
        print("{}: {}".format(args.topic, COMMAND_HELP[args.topic]))


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
        if argv and argv[0] in ("exec", "exec_exclusive"):
            _cmd_exec(argv[0], argv[1:])
            return
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
