"""dh_hl command-line entry point: argparse dispatch + help tool."""

import argparse
import sys

from . import safety
from . import tools
from . import build as build_mod
from .errors import DhHlError


# name -> one-line description (also drives `dh_hl help`)
COMMAND_HELP = {
    "help": "List commands, or describe one command.",
    "status": "Report whether the workspace matches a tracked schedule node.",
    "restore": "Copy a schedule node's C++ into the workspace + set current idea.",
    "build": "Compile the workspace and add/update its schedule node.",
    "profile": "Like build, but benchmark with the profiler over parameter sets.",
    "canon": "Make the current schedule the canonical schedule of the current idea.",
    "comment": "Attach commentary text to a schedule node.",
    "comment_importance": "Attach commentary with an integer importance value.",
    "new_root": "Create a new root schedule node from the workspace.",
    "set_idea": "Set the current idea state to an existing idea node.",
    "new_idea": "Add a child idea node (proposal) to a major schedule.",
    "list_ideas": "List the child idea nodes of a major schedule.",
    "list_sibling_schedules": "List schedules sharing a parent idea with the given schedule.",
    "list_child_schedules": "List the child schedules of an idea node.",
    "list_equal_schedules": "List schedules with the same source hash as the given one.",
    "view_idea": "Show an idea node's proposal and child schedules.",
    "force_parent_idea": "Parent a root schedule to an idea as its canonical (rare).",
    "json_schedule_info": "Dump a schedule node's full state as JSON.",
    "json_idea_info": "Dump an idea node's full state as JSON.",
    "history": "Walk from a schedule node up to its root, printing each hop.",
    "fix_canonical": "Resolve a canonical.txt merge conflict for an idea node.",
}


def _build_parser():
    p = argparse.ArgumentParser(prog="dh_hl", description="Dendritic Halide Harness")
    sub = p.add_subparsers(dest="command", metavar="command")

    def add(name, *, workspace=True):
        sp = sub.add_parser(name, help=COMMAND_HELP[name])
        if workspace:
            sp.add_argument("workspace", help="workspace C++ file name")
        return sp

    hp = sub.add_parser("help", help=COMMAND_HELP["help"])
    hp.add_argument("topic", nargs="?", help="command to describe")

    add("status")

    sp = add("restore")
    sp.add_argument("schedule", help="schedule ID")

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

    return p


_DISPATCH = {
    "status": tools.cmd_status,
    "restore": tools.cmd_restore,
    "build": build_mod.cmd_build,
    "profile": build_mod.cmd_profile,
    "canon": tools.cmd_canon,
    "comment": tools.cmd_comment,
    "comment_importance": tools.cmd_comment_importance,
    "new_root": tools.cmd_new_root,
    "set_idea": tools.cmd_set_idea,
    "new_idea": tools.cmd_new_idea,
    "list_ideas": tools.cmd_list_ideas,
    "list_sibling_schedules": tools.cmd_list_sibling_schedules,
    "list_child_schedules": tools.cmd_list_child_schedules,
    "list_equal_schedules": tools.cmd_list_equal_schedules,
    "view_idea": tools.cmd_view_idea,
    "force_parent_idea": tools.cmd_force_parent_idea,
    "json_schedule_info": tools.cmd_json_schedule_info,
    "json_idea_info": tools.cmd_json_idea_info,
    "history": tools.cmd_history,
    "fix_canonical": tools.cmd_fix_canonical,
}


def cmd_help(args):
    if args.topic is None:
        print("dh_hl commands:\n")
        for name in COMMAND_HELP:
            print("  {:20} {}".format(name, COMMAND_HELP[name]))
        print("\nUse `dh_hl help <command>` or `dh_hl <command> -h` for details.")
        return
    if args.topic not in COMMAND_HELP:
        raise DhHlError("no such command: " + args.topic)
    print("{}: {}".format(args.topic, COMMAND_HELP[args.topic]))


def main():
    safety.arm()
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(2)
    try:
        if args.command == "help":
            cmd_help(args)
        else:
            _DISPATCH[args.command](args)
    except DhHlError as e:
        print("dh_hl: " + str(e), file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:
        sys.exit(0)
