"""Implementations of the dh_hl subcommands (except build/profile, in build.py).

Each cmd_* function takes the parsed argparse namespace.  Mutating tools call
ctx.finish() as their final step so the deferred overwrites land and the
rollback handler is disarmed.
"""

import json
import os
import sys

from . import ids
from . import locks
from . import prompts
from . import safety
from .catalog import Catalog
from .context import (Context, SessionWorkspace, resolve_target,
                      _validate_catalog_dir, read_text_or_stdin)
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


def _current_idea_description(cis):
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


def _print_current_idea_details(catalog, cis):
    """If the current idea state names an idea, report on it: whether the idea
    node actually exists, and (if so) the status of its canonical schedule."""
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

_SUBAGENT_NO_WORKSPACE = """\
AGENTS: the current session is a sub-agent session,
but was not initialized with a schedule for you to edit.
DO NOT PROCEED and report back to the main agent,
unless you have been advised to do otherwise."""


def _print_no_workspace_advice(session):
    if session.depth != 0:
        print(_SUBAGENT_NO_WORKSPACE)
    elif session.is_self_closed():
        print("The current session is closed. Start a new one with")
        print("  dh_hl new_successor_session")
    else:
        print("To start editing a C++ schedule, consider one of")
        print("  dh_hl seed_schedule_short_id")
        print("to get the ID of a schedule to start editing, followed by")
        print("  dh_hl restore {schedule ID}")
        print("to initialize the workspace")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args):
    # Read-only: does NOT acquire the session lock (idea.md).
    ctx = Context.for_session(args, session_lock=False)
    catalog = ctx.catalog
    session = ctx.session
    ws = ctx.workspace

    print("Session: " + session.full_id)
    print("Parent session: " + (session.parent_id or "(none)"))
    print("Delisted: " + ("yes" if session.delisted else "no"))
    print("Seed idea: " + session.seed_idea_id)
    print("Output schedule: " + (session.output_schedule_id or "(none)"))

    cis = ws.current_idea_state
    print("Current idea state: " + _current_idea_description(cis))
    _print_current_idea_details(catalog, cis)

    if not ws.has_workspace():
        print("Status: no workspace C++ file")
        _print_no_workspace_advice(session)
        return

    h = ws.workspace_hash
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
    ctx = Context.for_session(args, session_lock=True)
    node = ctx.catalog.resolve_schedule(args.schedule)
    ws = ctx.workspace
    ws.ensure_private_dir()
    cis = ws.current_idea_state
    if node.is_root():
        cis.set_no_idea(node.timestamp)
    else:
        cis.set_idea(node.parent_id)
    # Overwrite workspace file last (deferred; never rolled back).
    from . import safety
    safety.queue_overwrite(ws.workspace_path, node.source)
    ctx.finish()
    print("Restored workspace from " + ctx.catalog.format_schedule_id(node))


# ---------------------------------------------------------------------------
# comment / comment_importance
# ---------------------------------------------------------------------------

def cmd_comment(args):
    ctx = Context.for_catalog(args)
    node = ctx.resolve_schedule_arg(args.schedule)
    text = _read_file_or_stdin(args.commentary)
    node.add_commentary(text, importance=None)
    ctx.finish()
    print("Added commentary to " + ctx.catalog.format_schedule_id(node))


def cmd_comment_importance(args):
    ctx = Context.for_catalog(args)
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
    ctx = Context.for_session(args, session_lock=True)
    catalog = ctx.catalog
    ws = ctx.workspace
    ws.require_workspace()
    ws.ensure_private_dir()
    h = ws.workspace_hash
    same_hash = [n for n in catalog.schedules.values() if n.hash == h]
    majors = [n for n in same_hash if n.is_major()]
    if majors:
        raise DhHlError(
            "workspace already stored as a major schedule; not creating a new "
            "root:\n  " + "\n  ".join(catalog.format_schedule_id(n)
                                      for n in majors))
    # Capture competing current-idea-state lines before overwriting.
    cis = ws.current_idea_state
    conflict_lines = list(cis.parsed_lines) if cis.kind == "conflict" else []

    node = catalog.create_schedule(ws.workspace_source, parent_idea=None)
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
    ctx = Context.for_session(args, session_lock=True)
    idea = ctx.catalog.resolve_idea(args.idea)
    ctx.workspace.ensure_private_dir()
    ctx.workspace.current_idea_state.set_idea(idea.full_id)
    ctx.finish()
    print("Current idea set to " + ctx.catalog.format_idea_id(idea))


# ---------------------------------------------------------------------------
# new_idea
# ---------------------------------------------------------------------------

def cmd_new_idea(args):
    ctx = Context.for_session(args, session_lock=True)
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
    ctx = Context.for_session(args, session_lock=True)
    catalog = ctx.catalog
    idea = ctx.current_idea_node()
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
    ctx = Context.for_catalog(args)
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
    ctx = Context.for_catalog(args)
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

def _print_idea_view(catalog, idea):
    print("=" * 72)
    print("Idea: " + idea.proposal_name)
    print("=" * 72)
    print(idea.proposal_text.rstrip("\n"))
    print("-" * 72)
    print("Child schedules:")
    children = catalog.child_schedules(idea)
    if not children:
        print("  (none)")
    for s in sorted(children, key=lambda n: n.timestamp):
        print("  " + catalog.format_schedule_id(s))


def cmd_view_idea(args):
    ctx = Context.for_catalog(args)
    _print_idea_view(ctx.catalog, ctx.catalog.resolve_idea(args.idea))


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
    ctx = Context.for_catalog(args)
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
    ctx = Context.for_catalog(args)
    node = ctx.resolve_schedule_arg(args.schedule)
    if node.is_root():
        raise DhHlError(
            "list_sibling_schedules needs a non-root schedule; a root node has "
            "no parent idea, hence no siblings")
    _print_schedule_list(ctx, ctx.catalog.child_schedules(node.parent_idea()))


def cmd_list_child_schedules(args):
    ctx = Context.for_catalog(args)
    idea = ctx.catalog.resolve_idea(args.idea)
    _print_schedule_list(ctx, ctx.catalog.child_schedules(idea))


def cmd_list_equal_schedules(args):
    ctx = Context.for_catalog(args)
    node = ctx.resolve_schedule_arg(args.schedule)
    equal = [n for n in ctx.catalog.schedules.values() if n.hash == node.hash]
    _print_schedule_list(ctx, equal)


# ---------------------------------------------------------------------------
# json_schedule_info / json_idea_info
# ---------------------------------------------------------------------------

def _schedule_json(catalog, node):
    return {
        "id": node.full_id,
        "parent": node.parent_id,
        "children": [i.full_id for i in catalog.child_ideas(node)],
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


def _idea_json(catalog, idea):
    imp = idea.importance
    return {
        "id": idea.full_id,
        "parent": idea.parent_id,
        "children": [s.full_id for s in catalog.child_schedules(idea)],
        "proposal_name": idea.proposal_name,
        "proposal_text": idea.proposal_text,
        "canonical_schedule": idea.canonical,
        "importance": None if imp == float("-inf") else imp,
    }


def _session_json(catalog, session):
    return {
        "id": session.full_id,
        "parent": session.parent_id,
        "children": [c.full_id for c in catalog.child_sessions(session)],
        "seed_idea": session.seed_idea_id,
        "output_schedule": session.output_schedule_id,
        "delisted": session.delisted,
        "depth": session.depth,
    }


def cmd_json_schedule_info(args):
    ctx = Context.for_catalog(args)
    node = ctx.resolve_schedule_arg(args.schedule)
    print(json.dumps(_schedule_json(ctx.catalog, node), indent=1))


def cmd_json_idea_info(args):
    ctx = Context.for_catalog(args)
    idea = ctx.catalog.resolve_idea(args.idea)
    print(json.dumps(_idea_json(ctx.catalog, idea), indent=1))


def cmd_json_session_info(args):
    # Read-only: does NOT acquire the session lock (idea.md).
    ctx = Context.for_session(args, session_lock=False)
    print(json.dumps(_session_json(ctx.catalog, ctx.session), indent=1))


def cmd_json_export(args):
    ctx = Context.for_catalog(args)
    catalog = ctx.catalog
    obj = {
        "ideas": {i.full_id: _idea_json(catalog, i)
                  for i in catalog.ideas.values()},
        "schedules": {s.full_id: _schedule_json(catalog, s)
                      for s in catalog.schedules.values()},
        "sessions": {s.full_id: _session_json(catalog, s)
                     for s in catalog.sessions.values()},
    }
    print(json.dumps(obj, indent=1))


# ---------------------------------------------------------------------------
# fix_canonical
# ---------------------------------------------------------------------------

def cmd_fix_canonical(args):
    ctx = Context.for_catalog(args)
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


# ===========================================================================
# Phase 4: session lifecycle, listing, copy/id-of, workspace, views
# ===========================================================================

# ---- session creation (the "Session Creation Common" flow) ----------------

def _create_session_and_idea(catalog, parent_schedule, proposal_name,
                             proposal_text, parent_session, depth):
    """Create the session/idea pair off *parent_schedule* (idea.md "Session
    Creation Tools: Common Information").  Returns (session, handle).

    Mints the session ID first so the seed idea's proposal text can reference
    it; seeds a session whose private workspace holds a copy of the parent
    schedule's C++ pointing at the new idea; and duplicates the parent schedule
    as the new idea's canonical, giving the new session an exclusive sub-tree."""
    session_id = catalog.mint_session_id(depth)
    text = proposal_text if proposal_text.endswith("\n") else proposal_text + "\n"
    text += "Created for session: {}\n".format(session_id)
    idea = catalog.create_idea(parent_schedule, proposal_name, text)
    session = catalog.create_session(idea, parent_session, depth,
                                     session_id=session_id)
    ws = SessionWorkspace(catalog.catalog_dir, session_id, catalog=catalog)
    ws.initialize(parent_schedule.source, ("idea", idea.full_id))
    dup = catalog.create_schedule(parent_schedule.source, parent_idea=idea)
    idea.set_canonical(dup.full_id)
    handle = locks.allocate_handle(catalog.catalog_dir, session_id)
    return session, handle


def cmd_new_catalog(args):
    catalog_dir = _validate_catalog_dir(args.catalog)
    if os.path.exists(catalog_dir):
        raise DhHlError("catalog directory already exists: " + catalog_dir)
    if not ids.is_proposal_name(args.proposal_name):
        raise DhHlError("proposal name must be 1..72 chars of [A-Za-z0-9_]: "
                        + repr(args.proposal_name))
    input_source = read_text_or_stdin(args.input_cpp)
    proposal_text = read_text_or_stdin(args.proposal)

    safety.makedirs_tracked(catalog_dir)
    locks.acquire_catalog(catalog_dir)
    catalog = Catalog(catalog_dir)
    catalog.ensure_created()
    root = catalog.create_schedule(input_source, parent_idea=None)
    session, handle = _create_session_and_idea(
        catalog, root, args.proposal_name, proposal_text,
        parent_session=None, depth=0)
    catalog.flush()
    safety.commit()
    print("Created catalog " + catalog_dir)
    print("Session: " + session.full_id)
    print("Session handle: " + handle)


def cmd_new_sub_session(args):
    ctx = Context.for_session(args, session_lock=True)
    parent_schedule = ctx.resolve_schedule_arg(args.schedule)
    proposal_text = read_text_or_stdin(args.proposal)
    session, handle = _create_session_and_idea(
        ctx.catalog, parent_schedule, args.proposal_name, proposal_text,
        parent_session=ctx.session, depth=ctx.session.depth + 1)
    ctx.finish()
    print("Created sub-session " + session.full_id)
    print("Session handle: " + handle)


def cmd_new_successor_session(args):
    ctx = Context.for_session(args, session_lock=True)
    session = ctx.session
    if session.depth != 0:
        raise DhHlError(
            "new_successor_session requires a top-level (depth 0) session")
    if not session.is_self_closed():
        raise DhHlError(
            "the current session must be self-closed (have an output schedule "
            "or be delisted) before starting a successor")
    if session.output_schedule_id is None:
        raise DhHlError(
            "the current session has no output schedule to succeed from "
            "(it was only delisted)")
    parent_schedule = ctx.catalog.get_schedule(session.output_schedule_id)
    proposal_text = read_text_or_stdin(args.proposal)
    new_session, handle = _create_session_and_idea(
        ctx.catalog, parent_schedule, args.proposal_name, proposal_text,
        parent_session=session, depth=0)
    ctx.finish()
    print("Created successor session " + new_session.full_id)
    print("Session handle: " + handle)


# ---- close / delist -------------------------------------------------------

def cmd_close_session(args):
    ctx = Context.for_session(args, session_lock=True)
    node = ctx.resolve_schedule_arg(args.schedule)
    session = ctx.session
    if session.output_schedule_id is not None:
        raise DhHlError(
            "the current session already has an output schedule: "
            + ctx.catalog.format_schedule_id(
                ctx.catalog.get_schedule(session.output_schedule_id)))
    if not any(c.importance is not None and c.importance > 0
               for c in node.commentary):
        raise DhHlError(
            "the output schedule must have commentary with positive importance;\n"
            "use `dh_hl comment_importance` to record a session summary first")
    session.set_output_schedule(node.full_id)
    ctx.finish()
    print("Closed session; output schedule "
          + ctx.catalog.format_schedule_id(node))


def cmd_delist_session(args):
    ctx = Context.for_session(args, session_lock=True)
    ctx.session.set_delisted()
    ctx.finish()
    print("Delisted session " + ctx.session.full_id)


# ---- listing --------------------------------------------------------------

def _print_session_line(catalog, session):
    print(session.full_id)
    print("  handle: "
          + locks.allocate_handle(catalog.catalog_dir, session.full_id))


def cmd_list_open_sessions(args):
    ctx = Context.for_catalog(args)
    catalog = ctx.catalog
    opens = [s for s in catalog.sessions.values()
             if not catalog.session_is_closed(s)]
    if not opens:
        print("(no open sessions)")
    for s in sorted(opens, key=lambda s: s.timestamp):
        _print_session_line(catalog, s)


def cmd_list_termini(args):
    ctx = Context.for_catalog(args)
    catalog = ctx.catalog
    termini = [s for s in catalog.sessions.values()
               if catalog.session_is_terminus(s)]
    if not termini:
        print("(no termini)")
    for s in sorted(termini, key=lambda s: s.timestamp):
        _print_session_line(catalog, s)


# ---- copy / id-of schedule nouns ------------------------------------------

def _the_terminus_output(catalog):
    termini = [s for s in catalog.sessions.values()
               if catalog.session_is_terminus(s)]
    if len(termini) != 1:
        raise DhHlError(
            "expected exactly one terminus, found {}".format(len(termini)))
    term = termini[0]
    if term.output_schedule_id is None:
        raise DhHlError("the terminus session has no output schedule")
    return catalog.get_schedule(term.output_schedule_id)


def _session_seed_schedule(ctx):
    idea = ctx.catalog.get_idea(ctx.session.seed_idea_id)
    if idea.canonical is None:
        raise DhHlError("the session's seed idea has no canonical schedule")
    return ctx.catalog.get_schedule(idea.canonical)


def _session_output_schedule(ctx):
    sid = ctx.session.output_schedule_id
    if sid is None:
        raise DhHlError("the current session has no output schedule yet")
    return ctx.catalog.get_schedule(sid)


def _write_output(path, text):
    """Write a schedule's C++ to *path*; '-' means stdout."""
    if path == "-":
        sys.stdout.write(text)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def cmd_copy_schedule(args):
    ctx = Context.for_catalog(args)
    _write_output(args.output, ctx.resolve_schedule_arg(args.schedule).source)


def cmd_copy_terminus_schedule(args):
    ctx = Context.for_catalog(args)
    _write_output(args.output, _the_terminus_output(ctx.catalog).source)


def cmd_copy_session_seed_schedule(args):
    ctx = Context.for_session(args, session_lock=False)
    _write_output(args.output, _session_seed_schedule(ctx).source)


def cmd_copy_session_output(args):
    ctx = Context.for_session(args, session_lock=False)
    _write_output(args.output, _session_output_schedule(ctx).source)


def cmd_terminus_schedule_full_id(args):
    ctx = Context.for_catalog(args)
    print(_the_terminus_output(ctx.catalog).full_id)


def cmd_terminus_schedule_short_id(args):
    ctx = Context.for_catalog(args)
    print(ctx.catalog.format_schedule_id(_the_terminus_output(ctx.catalog)))


def cmd_seed_schedule_full_id(args):
    ctx = Context.for_session(args, session_lock=False)
    print(_session_seed_schedule(ctx).full_id)


def cmd_seed_schedule_short_id(args):
    ctx = Context.for_session(args, session_lock=False)
    print(ctx.catalog.format_schedule_id(_session_seed_schedule(ctx)))


def cmd_session_output_full_id(args):
    ctx = Context.for_session(args, session_lock=False)
    print(_session_output_schedule(ctx).full_id)


def cmd_session_output_short_id(args):
    ctx = Context.for_session(args, session_lock=False)
    print(ctx.catalog.format_schedule_id(_session_output_schedule(ctx)))


# ---- workspace location ---------------------------------------------------

def cmd_workspace_schedule(args):
    ctx = Context.for_session(args, session_lock=False)
    ctx.session  # validate the session exists
    print(ctx.workspace.workspace_path)


def cmd_workspace_bin(args):
    ctx = Context.for_session(args, session_lock=False)
    ctx.session
    print(ctx.workspace.bin_dir)


# ---- ID translation -------------------------------------------------------

def cmd_schedule_full_id(args):
    ctx = Context.for_catalog(args)
    print(ctx.resolve_schedule_arg(args.schedule).full_id)


def cmd_schedule_short_id(args):
    ctx = Context.for_catalog(args)
    print(ctx.catalog.format_schedule_id(ctx.resolve_schedule_arg(args.schedule)))


def cmd_idea_full_id(args):
    ctx = Context.for_catalog(args)
    print(ctx.catalog.resolve_idea(args.idea).full_id)


def cmd_idea_short_id(args):
    ctx = Context.for_catalog(args)
    print(ctx.catalog.format_idea_id(ctx.catalog.resolve_idea(args.idea)))


def cmd_session_full_id(args):
    ctx = Context.for_session(args, session_lock=False)
    print(ctx.session.full_id)


def cmd_session_handle(args):
    ctx = Context.for_session(args, session_lock=False)
    # Never fall back to a full ID: a handle encodes the catalog dir too.
    print(locks.allocate_handle(ctx.catalog.catalog_dir, ctx.session.full_id))


# ---- views ----------------------------------------------------------------

def cmd_view_session_idea(args):
    ctx = Context.for_session(args, session_lock=False)
    _print_idea_view(ctx.catalog, ctx.catalog.get_idea(ctx.session.seed_idea_id))


def _print_commentary(node, positive_only=False):
    comments = sorted(node.commentary, key=lambda c: c.timestamp)
    if positive_only:
        comments = [c for c in comments
                    if c.importance is not None and c.importance > 0]
    if not comments:
        print("(no commentary)")
    for c in comments:
        print("=" * 72)
        print("timestamp: " + c.timestamp)
        print("importance: "
              + ("none" if c.importance is None else str(c.importance)))
        print("-" * 72)
        print(c.text.rstrip("\n"))


def cmd_view_commentary(args):
    ctx = Context.for_catalog(args)
    _print_commentary(ctx.resolve_schedule_arg(args.schedule))


def cmd_view_session_commentary(args):
    ctx = Context.for_session(args, session_lock=False)
    _print_commentary(_session_output_schedule(ctx), positive_only=True)


# ---- prompt ---------------------------------------------------------------

def cmd_prompt(args):
    """Emit the assembled agent prompt.  The audience is given explicitly and is
    NEVER inferred from the session, so the prompt can serve as an independent
    double-check of the agent's role (main vs sub).  Needs no catalog/session."""
    if bool(args.main) == bool(args.sub):
        raise DhHlError("prompt requires exactly one of --main / --sub")
    sys.stdout.write(prompts.load_prompt("main" if args.main else "sub"))
