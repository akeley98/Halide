"""Implementations of the dh_hl subcommands (except build/profile, in build.py).

Each cmd_* function takes the parsed argparse namespace.  Mutating tools call
ctx.finish() as their final step so the deferred overwrites land and the
rollback handler is disarmed.
"""

import json

from .context import Context, read_text_or_stdin
from .errors import DhHlError


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# Backwards-compatible alias; "-" -> stdin handling now lives in context.
_read_file_or_stdin = read_text_or_stdin


def _first_line_72(text):
    first = text.split("\n", 1)[0]
    return first[:72]


def _print_idea_listing(ctx, idea, marker=""):
    """The 3-line idea summary shared by list_ideas and history."""
    print("{}{}".format(marker, ctx.catalog.format_idea_id(idea)))
    print("  " + idea.proposal_name)
    print("  " + _first_line_72(idea.proposal_text))


def _current_idea_description(catalog):
    cis = catalog.current_idea_state
    if cis.kind == "missing":
        return "missing"
    if cis.kind == "no_idea":
        return "no current idea (root, timestamp {})".format(cis.timestamp)
    if cis.kind == "idea":
        return "current idea: {}".format(cis.idea_id)
    # conflict
    if cis.parsed_lines:
        return "PARSE ERROR / merge conflict; competing states:\n  " \
            + "\n  ".join(cis.parsed_lines)
    return "PARSE ERROR: no valid state could be parsed"


def _print_current_idea_details(catalog):
    """If the current idea state names an idea, report on it: whether the idea
    node actually exists, and (if so) the status of its canonical schedule."""
    cis = catalog.current_idea_state
    if cis.kind != "idea":
        return
    idea = catalog.ideas.get(cis.idea_id)
    if idea is None:
        # Syntactically valid but dangling -- defensive helpfulness in case the
        # current idea state ever lives outside git / gets out of sync.
        print("  WARNING: current idea state references a nonexistent idea node:")
        print("    " + cis.idea_id)
        return
    canon = idea.canonical
    if canon is None:
        print("  Current idea's canonical schedule: none")
    elif canon in catalog.schedules:
        print("  Current idea's canonical schedule: "
              + catalog.format_schedule_id(catalog.schedules[canon]))
    else:
        print("  Current idea's canonical schedule: " + canon + " (missing!)")


INCONSISTENT_WARNING = """\
AGENTS: If this is the first time editing this file this session,
this means the file was edited without correct harness tracking.
DO NOT PROCEED, unless you have been advised otherwise.
Likely causes include user action, and git checkouts / merges."""


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    if not ctx.catalog.exists():
        print("No catalog directory yet.")
        print("Advice: run `dh_hl new_root {}`".format(args.workspace))
        return
    catalog = ctx.catalog
    print("Current idea state: " + _current_idea_description(catalog))
    _print_current_idea_details(catalog)

    h = ctx.workspace_hash
    matching = [n for n in catalog.schedules.values() if n.hash == h]
    if not matching:
        print("Status: workspace inconsistent, unknown schedule")
        print(INCONSISTENT_WARNING)
        return

    node = ctx.unambiguous_schedule()
    if node is not None:
        print("Status: workspace consistent")
        print("Schedule node: " + catalog.format_schedule_id(node))
        return

    print("Status: workspace inconsistent, unexpected current idea state")
    print(INCONSISTENT_WARNING)


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

def cmd_restore(args):
    ctx = Context(args.workspace)
    # restore is the one tool that may run without an existing workspace file.
    ctx.require_catalog_ro()
    node = ctx.catalog.resolve_schedule(args.schedule)
    cis = ctx.catalog.current_idea_state
    if node.is_root():
        cis.set_no_idea(node.timestamp)
    else:
        cis.set_idea(node.parent_id)
    # Overwrite workspace file last (deferred via write_allowed-style overwrite).
    from . import safety
    safety.queue_overwrite(ctx.workspace_path, node.source)
    ctx.finish()
    print("Restored workspace from " + ctx.catalog.format_schedule_id(node))


# ---------------------------------------------------------------------------
# comment / comment_importance
# ---------------------------------------------------------------------------

def cmd_comment(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.ensure_catalog_rw()
    node = ctx.resolve_schedule_arg(args.schedule)
    text = _read_file_or_stdin(args.commentary)
    node.add_commentary(text, importance=None)
    ctx.finish()
    print("Added commentary to " + ctx.catalog.format_schedule_id(node))


def cmd_comment_importance(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.ensure_catalog_rw()
    node = ctx.resolve_schedule_arg(args.schedule)
    text = _read_file_or_stdin(args.commentary)
    node.add_commentary(text, importance=args.importance)
    ctx.finish()
    print("Added commentary (importance {}) to {}".format(
        args.importance, ctx.catalog.format_schedule_id(node)))


# ---------------------------------------------------------------------------
# new_root
# ---------------------------------------------------------------------------

def cmd_new_root(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.ensure_catalog_rw()
    catalog = ctx.catalog
    h = ctx.workspace_hash
    same_hash = [n for n in catalog.schedules.values() if n.hash == h]
    majors = [n for n in same_hash if n.is_major()]
    if majors:
        raise DhHlError(
            "workspace already stored as a major schedule; not creating a new "
            "root:\n  " + "\n  ".join(catalog.format_schedule_id(n)
                                      for n in majors))
    # Capture competing current-idea-state lines before overwriting.
    cis = catalog.current_idea_state
    conflict_lines = list(cis.parsed_lines) if cis.kind == "conflict" else []

    node = catalog.create_schedule(ctx.workspace_source, parent_idea=None)
    cis.set_no_idea(node.timestamp)

    if conflict_lines:
        node.add_commentary(
            "dh_hl new_root tool: automated merge conflict recovery\n"
            + "\n".join(conflict_lines) + "\n",
            importance=None)

    ctx.finish()
    print("Created root schedule " + catalog.format_schedule_id(node))


# ---------------------------------------------------------------------------
# set_idea
# ---------------------------------------------------------------------------

def cmd_set_idea(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.ensure_catalog_rw()
    idea = ctx.catalog.resolve_idea(args.idea)
    ctx.catalog.current_idea_state.set_idea(idea.full_id)
    ctx.finish()
    print("Current idea set to " + ctx.catalog.format_idea_id(idea))


# ---------------------------------------------------------------------------
# new_idea
# ---------------------------------------------------------------------------

def cmd_new_idea(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.ensure_catalog_rw()
    node = ctx.resolve_schedule_arg(args.schedule)
    if not node.is_major():
        raise DhHlError(_minor_schedule_advice(ctx.catalog, node))
    text = _read_file_or_stdin(args.proposal)
    idea = ctx.catalog.create_idea(node, args.proposal_name, text)
    ctx.finish()
    print("Created idea " + ctx.catalog.format_idea_id(idea))


def _minor_schedule_advice(catalog, node):
    """Actionable message when new_idea targets a minor schedule (its parent
    must be a major schedule).  A minor schedule is never a root, so it always
    has a parent idea."""
    parent = node.parent_idea()
    lines = ["cannot add an idea to a minor schedule ({}); ideas must hang off "
             "a major schedule.".format(catalog.format_schedule_id(node))]
    if parent.canonical is not None:
        canon = catalog.format_schedule_id(catalog.schedules[parent.canonical])
        lines.append(
            "Its parent idea's canonical schedule is {0}; branch the new idea "
            "off that instead:\n    dh_hl new_idea {0} <name> <proposal file>"
            .format(canon))
    else:
        lines.append(
            "Its parent idea has no canonical schedule yet. If this schedule "
            "builds and you're happy it implements the idea, run `dh_hl canon` "
            "to make it canonical (then it can host new ideas).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# canon
# ---------------------------------------------------------------------------

def cmd_canon(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.ensure_catalog_rw()
    catalog = ctx.catalog
    idea = catalog.current_idea_node()
    if idea is None:
        raise DhHlError("no current idea node; nothing to make canonical for")
    node = ctx.require_unambiguous_schedule()
    if node.result != "success":
        raise DhHlError(
            "schedule must have result 'success' to be canonical (is {!r})"
            .format(node.result))
    if idea.canonical is not None:
        if idea.canonical == node.full_id:
            raise DhHlError("this schedule is already the canonical schedule")
        blocker = catalog.format_schedule_id(catalog.schedules[idea.canonical])
        raise DhHlError(
            "idea already has a canonical schedule ({0}).\n"
            "To record the current schedule as a variation, branch a new idea "
            "off that canonical schedule and explore under it:\n"
            "    dh_hl new_idea {0} <name> <proposal file>\n"
            "    dh_hl set_idea <the new idea's ID>\n"
            "then rebuild and `dh_hl canon`.".format(blocker))
    # Sanity: canon target should be a child of the current idea.
    if node.parent_id != idea.full_id:
        raise DhHlError("schedule is not a child of the current idea")
    idea.set_canonical(node.full_id)
    ctx.finish()
    print("Set canonical schedule of {} to {}".format(
        catalog.format_idea_id(idea), catalog.format_schedule_id(node)))


# ---------------------------------------------------------------------------
# force_parent_idea
# ---------------------------------------------------------------------------

def cmd_force_parent_idea(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.ensure_catalog_rw()
    catalog = ctx.catalog
    idea = catalog.resolve_idea(args.idea)
    node = ctx.resolve_schedule_arg(args.schedule)
    if not node.is_root():
        raise DhHlError("force_parent_idea requires a root schedule node")
    if idea.canonical is not None:
        raise DhHlError("idea already has a canonical schedule")
    if idea.timestamp >= node.timestamp:
        raise DhHlError(
            "tree invariant violation: idea's parent schedule (timestamp {}) "
            "is not older than the schedule being parented (timestamp {})"
            .format(idea.timestamp, node.timestamp))
    node.set_parent_existing_root(idea.full_id)
    idea.set_canonical(node.full_id)
    if catalog._linked:
        idea.child_schedule_ids.append(node.full_id)
    ctx.finish()
    print("Parented {} to idea {} (as canonical)".format(
        catalog.format_schedule_id(node), catalog.format_idea_id(idea)))


# ---------------------------------------------------------------------------
# list_ideas
# ---------------------------------------------------------------------------

def cmd_list_ideas(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.require_catalog_ro()
    node = ctx.resolve_schedule_arg(args.schedule)
    if not node.is_major():
        raise DhHlError("schedule node is not a major schedule")
    ideas = ctx.catalog.child_ideas(node)
    if not ideas:
        print("(no child ideas)")
    for idea in sorted(ideas, key=lambda i: i.full_id):
        _print_idea_listing(ctx, idea)


# ---------------------------------------------------------------------------
# view_idea
# ---------------------------------------------------------------------------

def cmd_view_idea(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.require_catalog_ro()
    idea = ctx.catalog.resolve_idea(args.idea)
    print("=" * 72)
    print("Idea: " + idea.proposal_name)
    print("=" * 72)
    print(idea.proposal_text.rstrip("\n"))
    print("-" * 72)
    print("Child schedules:")
    children = ctx.catalog.child_schedules(idea)
    if not children:
        print("  (none)")
    for s in sorted(children, key=lambda n: n.timestamp):
        print("  " + ctx.catalog.format_schedule_id(s))


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

def _print_schedule_node(ctx, node, marked_idea_id=None):
    """Print one schedule node's block: an ID header, its child ideas (in
    `list_ideas` format, optionally marking one), and its commentary.  The
    leading `#` rule doubles as the separator between blocks.  Shared by
    `history` and the list-schedules tools."""
    catalog = ctx.catalog
    print("#" * 72)
    print("Schedule: " + catalog.format_schedule_id(node))
    print("-" * 72)  # "-" keeps the ID visually attached to its own contents

    ideas = catalog.child_ideas(node)
    if ideas:
        print("Ideas:")
    for idea in sorted(ideas, key=lambda i: i.full_id):
        marker = "* " if idea.full_id == marked_idea_id else "  "
        _print_idea_listing(ctx, idea, marker=marker)

    if node.commentary:
        print("Commentary:")
    for c in sorted(node.commentary, key=lambda c: c.timestamp):
        print("  " + c.timestamp)
        print("  " + _first_line_72(c.text))


def cmd_history(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.require_catalog_ro()
    node = ctx.resolve_schedule_arg(args.schedule)
    prev_idea_id = None  # the idea whose parent is the previously printed sched
    while node is not None:
        _print_schedule_node(ctx, node, marked_idea_id=prev_idea_id)
        if node.is_root():
            break
        idea = node.parent_idea()
        prev_idea_id = idea.full_id
        parent_schedule = idea.parent_schedule()
        # Tree timestamp invariant: an idea's parent schedule is strictly older
        # than its child schedules.  (An idea's implicit timestamp equals its
        # parent schedule's, so this is the only edge to check.)  Guarantees we
        # move strictly down in timestamp, so no infinite loop on a cooked tree.
        if not (parent_schedule.timestamp < node.timestamp):
            print("!! tree timestamp invariant violated walking up; stopping")
            break
        node = parent_schedule


# ---------------------------------------------------------------------------
# list_sibling_schedules / list_child_schedules / list_equal_schedules
# ---------------------------------------------------------------------------

def _print_schedule_list(ctx, nodes):
    ordered = sorted(nodes, key=lambda n: n.timestamp)
    if not ordered:
        print("(no matching schedule nodes)")
        return
    for n in ordered:
        _print_schedule_node(ctx, n)


def cmd_list_sibling_schedules(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.require_catalog_ro()
    node = ctx.resolve_schedule_arg(args.schedule)
    if node.is_root():
        raise DhHlError(
            "list_sibling_schedules needs a non-root schedule; a root node has "
            "no parent idea, hence no siblings")
    _print_schedule_list(ctx, ctx.catalog.child_schedules(node.parent_idea()))


def cmd_list_child_schedules(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.require_catalog_ro()
    idea = ctx.catalog.resolve_idea(args.idea)
    _print_schedule_list(ctx, ctx.catalog.child_schedules(idea))


def cmd_list_equal_schedules(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.require_catalog_ro()
    node = ctx.resolve_schedule_arg(args.schedule)
    equal = [n for n in ctx.catalog.schedules.values() if n.hash == node.hash]
    _print_schedule_list(ctx, equal)


# ---------------------------------------------------------------------------
# json_schedule_info / json_idea_info
# ---------------------------------------------------------------------------

def cmd_json_schedule_info(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.require_catalog_ro()
    catalog = ctx.catalog
    node = ctx.resolve_schedule_arg(args.schedule)
    children = [i.full_id for i in catalog.child_ideas(node)]
    obj = {
        "id": node.full_id,
        "parent": node.parent_id,
        "children": children,
        "source": node.source,
        "timestamp": node.timestamp,
        "hash": node.hash,
        "result": node.result,
        "benchmark": [b.data for b in node.benchmarks],
        "commentary": [
            {"timestamp": c.timestamp, "importance": c.importance,
             "text": c.text}
            for c in sorted(node.commentary, key=lambda c: c.timestamp)
        ],
    }
    print(json.dumps(obj, indent=1))


def cmd_json_idea_info(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.require_catalog_ro()
    catalog = ctx.catalog
    idea = catalog.resolve_idea(args.idea)
    children = [s.full_id for s in catalog.child_schedules(idea)]
    imp = idea.importance
    obj = {
        "id": idea.full_id,
        "parent": idea.parent_id,
        "children": children,
        "proposal_name": idea.proposal_name,
        "proposal_text": idea.proposal_text,
        "canonical_schedule": idea.canonical,
        "importance": None if imp == float("-inf") else imp,
    }
    print(json.dumps(obj, indent=1))


# ---------------------------------------------------------------------------
# fix_canonical
# ---------------------------------------------------------------------------

def cmd_fix_canonical(args):
    ctx = Context(args.workspace)
    ctx.require_workspace()
    ctx.ensure_catalog_rw()
    catalog = ctx.catalog
    idea = catalog.resolve_idea(args.idea)
    lines = idea.canonical_lines()
    if len(lines) != 2:
        raise DhHlError(
            "expected exactly 2 competing canonical IDs in canonical.txt, "
            "found {}".format(len(lines)))
    a, b = lines
    for x in (a, b):
        if x not in catalog.schedules:
            raise DhHlError("canonical.txt references unknown schedule: " + x)
    older, newer = sorted([a, b])  # timestamps sort lexicographically
    older_node = catalog.get_schedule(older)
    newer_node = catalog.get_schedule(newer)

    ts = catalog.fresh_timestamp()
    proposal_name = "fix_canonical_{}".format(ts).replace("-", "").replace(":", "")
    proposal_name = proposal_name.replace("T", "_")[:72]
    proposal_text = (
        "Auto-generated by `dh_hl fix_canonical` to resolve a merge conflict "
        "between two competing canonical schedules of this idea.\n"
        "Older canonical kept here; newer moved under this idea.\n")

    # Older schedule becomes the (single) canonical of the referenced idea.
    idea.set_canonical(older)
    # Add a child idea under the older schedule whose canonical is the newer.
    if not older_node.is_major():
        raise DhHlError("older canonical schedule is not major; cannot attach "
                        "resolution idea")
    fix_idea = catalog.create_idea(older_node, proposal_name, proposal_text)
    catalog.reparent_existing_schedule(fix_idea, newer_node)
    fix_idea.set_canonical(newer)

    ctx.finish()
    print("Resolved canonical conflict for idea {}".format(
        catalog.format_idea_id(idea)))
    print("  older canonical: " + catalog.format_schedule_id(older_node))
    print("  newer moved under new idea: " + catalog.format_idea_id(fix_idea))
