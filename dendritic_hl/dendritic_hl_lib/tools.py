"""Implementations of the dh_hl subcommands (except build/profile, in build.py).

Each cmd_* function takes the parsed argparse namespace.  Mutating tools call
ctx.finish() as their final step so the deferred overwrites land and the
rollback handler is disarmed.
"""

import json
import os
import re
import sys

from . import build
from . import cost
from . import ids
from . import locks
from . import profiler_stats
from . import profiler_warnings
from . import prompts
from . import safety
from .catalog import (Catalog, COMMENTARY_REVIEWS, DEFAULT_PARAMETERS,
                      IDEA_SIDE_LINK_TYPES, canonical_block_advice,
                      dump_parameters, load_parameters_text)
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


def _last_nonempty_line(text):
    for line in reversed(text.split("\n")):
        if line.strip():
            return line.strip()
    return ""


def _print_idea_listing(ctx, idea, marker="", include_proposal_text=True):
    """The idea summary shared by list_child_ideas, history, and the private-idea
    listings (idea.md "List Ideas Tools"): the idea ID, then a `canonical: ` line
    and a `proposal: ` line, then (unless *include_proposal_text* is False, as for
    list_seed_ideas) the proposal-text first line + any Created-for-session line,
    then the side links."""
    catalog = ctx.catalog
    print("{}{}".format(marker, catalog.format_idea_id(idea)))
    if idea.canonical is None:
        canonical = "(none)"
    elif idea.canonical in catalog.schedules:
        canonical = catalog.format_schedule_id(catalog.schedules[idea.canonical])
    else:
        canonical = idea.canonical  # dangling (e.g. git checkout desync)
    print("  canonical: " + canonical)
    print("  proposal: " + idea.proposal_name)
    if include_proposal_text:
        print("  " + _first_line_72(idea.proposal_text))
        last = _last_nonempty_line(idea.proposal_text)
        if last.startswith("Created for session:"):
            print("  " + last)
    for line in _side_link_lines(catalog, idea):
        print("  " + line)


_SIDE_LINK_LABELS = {"borrows_from": "borrowed from",
                     "superseded_by": "superseded by"}


def _side_link_lines(catalog, idea):
    """One "borrowed from: {id}" / "superseded by: {id}" line per outgoing side
    link, using the destination idea's short ID when it resolves."""
    lines = []
    for link_type, dest in idea.side_links:
        dest_idea = catalog.ideas.get(dest)
        dest_id = catalog.format_idea_id(dest_idea) if dest_idea else dest
        lines.append("{}: {}".format(_SIDE_LINK_LABELS[link_type], dest_id))
    return lines


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
    print("Session: " + ("closed" if catalog.session_is_closed(session)
                         else "open"))

    cis = ws.current_idea_state
    print("Current idea state: " + _current_idea_description(cis))
    _print_current_idea_details(catalog, cis)

    missing = ws.missing_workspace_files()
    if missing:
        print("Status: missing workspace " + " and ".join(missing))
        print("AGENTS: run `dh_hl init_workspace` to get files to edit")
        return

    h = ws.workspace_hash
    matching = [n for n in catalog.schedules.values() if n.hash == h]
    if not matching:
        print("Status: workspace inconsistent, unknown schedule")
        return

    node = ctx.unambiguous_schedule()
    if node is not None:
        print("Status: workspace consistent")
        print("Schedule node: " + catalog.format_schedule_id(node))
        return

    print("Status: workspace inconsistent, unexpected current idea state")


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

def cmd_restore_schedule(args):
    ctx = Context.for_session(args, session_lock=True)
    node = ctx.catalog.resolve_schedule(args.schedule)
    ws = ctx.workspace
    ws.ensure_private_dir()
    cis = ws.current_idea_state
    if node.is_root():
        cis.set_no_idea(node.timestamp)
    else:
        cis.set_idea(node.parent_id)
    # Overwrite workspace files last (deferred; never rolled back).  Copy the
    # parameters verbatim too, so the workspace round-trips to the same hash.
    from . import safety
    safety.queue_overwrite(ws.workspace_path, node.source)
    safety.queue_overwrite(ws.params_path, node.params_text)
    ctx.finish()
    print("Restored workspace from " + ctx.catalog.format_schedule_id(node))


# ---------------------------------------------------------------------------
# restore_idea
# ---------------------------------------------------------------------------

def cmd_restore_idea(args):
    ctx = Context.for_session(args, session_lock=True)
    catalog = ctx.catalog
    idea = catalog.resolve_idea(args.idea)
    parent = idea.parent_schedule()
    ws = ctx.workspace
    ws.ensure_private_dir()
    ws.current_idea_state.set_idea(idea.full_id)
    # Overwrite workspace files last (deferred; never rolled back).
    from . import safety
    safety.queue_overwrite(ws.workspace_path, parent.source)
    safety.queue_overwrite(ws.params_path, parent.params_text)
    ctx.finish()
    print("Restored workspace from idea {}'s parent schedule {}".format(
        catalog.format_idea_id(idea), catalog.format_schedule_id(parent)))
    print("(ready to implement the idea; `dh_hl status` will read inconsistent, "
          "which is normal)")
    if idea.canonical is not None:
        canon_id = (catalog.format_schedule_id(catalog.schedules[idea.canonical])
                    if idea.canonical in catalog.schedules else idea.canonical)
        print("WARNING: this idea already has a canonical schedule: " + canon_id)
        print("  To start from that implementation instead, use:")
        print("    dh_hl restore_schedule -s ... " + canon_id)


# ---------------------------------------------------------------------------
# comment
# ---------------------------------------------------------------------------

def cmd_comment(args):
    ctx = Context.for_catalog(args)
    node = ctx.resolve_schedule_arg(args.schedule)
    text = _read_file_or_stdin(args.commentary)
    review = getattr(args, "review", None) or "neutral"
    if review not in COMMENTARY_REVIEWS:
        raise DhHlError(
            "--review must be one of {} (not 'mixed', which is a derived "
            "schedule review only)".format(", ".join(COMMENTARY_REVIEWS)))
    # Resolve each --cancels target and require it to belong to THIS schedule
    # node (a commentary can only cancel same-node commentary).
    cancels = []
    for cid in getattr(args, "cancels", None) or []:
        target = ctx.catalog.resolve_commentary(cid)
        if target.schedule.full_id != node.full_id:
            raise DhHlError(
                "--cancels target {} is not a commentary of schedule {}; a "
                "commentary can only cancel others on the same schedule node"
                .format(ctx.catalog.format_commentary_id(target),
                        ctx.catalog.format_schedule_id(node)))
        cancels.append(target.local_id)
    c = node.add_commentary(text, review=review, cancels=cancels)
    ctx.finish()
    # Print the new commentary's ID so it can be cited (e.g. by a WarningToggle).
    print("Added {} commentary {} to {}".format(
        review, ctx.catalog.format_commentary_id(c),
        ctx.catalog.format_schedule_id(node)))


# ---------------------------------------------------------------------------
# new_root
# ---------------------------------------------------------------------------

def cmd_new_root(args):
    ctx = Context.for_session(args, session_lock=True)
    catalog = ctx.catalog
    ws = ctx.workspace
    ws.require_workspace_files()
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

    node = catalog.create_schedule(ws.workspace_source, parent_idea=None,
                                   params_text=ws.workspace_params_text)
    cis.set_no_idea(node.timestamp)

    if conflict_lines:
        node.add_commentary(
            "dh_hl new_root tool: automated merge conflict recovery\n"
            + "\n".join(conflict_lines) + "\n")  # default neutral review

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
    # Resolve the pool tag BEFORE creating the idea, so a --pool-tag-required
    # failure leaves nothing half-created.
    pool_tag = getattr(args, "pool_tag", None)
    if pool_tag is None:
        pool_tag = _inherit_pool_tag(ctx, node)
    idea = ctx.catalog.create_idea(node, args.proposal_name, text)
    ctx.workspace.set_pool_tag(idea.full_id, pool_tag)
    ctx.finish()
    print("Created idea " + ctx.catalog.format_idea_id(idea))


def _inherit_pool_tag(ctx, node):
    """The pool tag a new child idea of *node* inherits when --pool-tag is
    omitted: the pool tag of *node*'s parent idea, read from the private idea
    list.  Errors (--pool-tag required) if *node* is a root (no parent idea) or
    its parent idea isn't in the private idea list (idea.md New Idea Tool)."""
    if node.is_root():
        raise DhHlError(
            "--pool-tag is required: {} is a root node, so there is no parent "
            "idea to inherit a pool tag from".format(
                ctx.catalog.format_schedule_id(node)))
    parent_idea = node.parent_idea()
    ws = ctx.workspace
    if not ws.has_private_idea(parent_idea.full_id):
        raise DhHlError(
            "--pool-tag is required: parent idea {} is not in this session's "
            "private idea list, so its pool tag can't be inherited".format(
                ctx.catalog.format_idea_id(parent_idea)))
    return ws.get_pool_tag(parent_idea.full_id)


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
            "off that instead:\n    dh_hl new_idea <name> <proposal file> {0}"
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
        raise DhHlError(canonical_block_advice(catalog, idea.canonical))
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

def cmd_list_child_ideas(args):
    ctx = Context.for_catalog(args)
    node = ctx.resolve_schedule_arg(args.schedule)
    if not node.is_major():
        raise DhHlError("schedule node is not a major schedule")
    ideas = ctx.catalog.child_ideas(node)
    if not ideas:
        print("(no child ideas)")
    for idea in sorted(ideas, key=lambda i: i.full_id):
        _print_idea_listing(ctx, idea)


def cmd_list_seed_ideas(args):
    # Read-only over git-tracked state (idea.md marks it session-lock-free).
    ctx = Context.for_session(args, session_lock=False)
    _print_seed_ideas(ctx)


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
    for line in _side_link_lines(catalog, idea):
        print(line)


def cmd_view_idea(args):
    ctx = Context.for_catalog(args)
    _print_idea_view(ctx.catalog, ctx.catalog.resolve_idea(args.idea))


# ---------------------------------------------------------------------------
# add_idea_side_link
# ---------------------------------------------------------------------------

def cmd_add_idea_side_link(args):
    ctx = Context.for_catalog(args)
    if args.type not in IDEA_SIDE_LINK_TYPES:
        raise DhHlError("link type must be one of {}".format(
            ", ".join(IDEA_SIDE_LINK_TYPES)))
    lhs = ctx.catalog.resolve_idea(args.idea_lhs)
    rhs = ctx.catalog.resolve_idea(args.idea_rhs)
    added = lhs.add_side_link(args.type, rhs.full_id)
    ctx.finish()
    if added:
        print("Added side link: {} {} {}".format(
            ctx.catalog.format_idea_id(lhs), args.type,
            ctx.catalog.format_idea_id(rhs)))
    else:
        print("Side link already present (no-op)")


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
# root_of / session_root_of
# ---------------------------------------------------------------------------

def _session_root_schedule(catalog, seed_idea_ids, node):
    """The nearest ancestor schedule of *node* (inclusive) whose parent idea is
    a seed idea in *seed_idea_ids*, or None if none is found up to the root.
    Walks nearest-first, so with nested seed ideas the deeper one wins (idea.md
    session_root_of note).  schedule_path_to_root carries the loop guard."""
    seeds = set(seed_idea_ids)
    for n in catalog.schedule_path_to_root(node):
        pid = n.parent_id  # parent idea full ID (None for a root node)
        if pid is not None and pid in seeds:
            return n
    return None


def cmd_root_of(args):
    # Does not acquire the session lock (idea.md).
    ctx = Context.for_catalog(args)
    node = ctx.resolve_schedule_arg(args.schedule)
    root = ctx.catalog.schedule_path_to_root(node)[-1]
    print(ctx.catalog.format_schedule_id(root))


def cmd_session_root_of(args):
    # Does not acquire the session lock (idea.md).
    ctx = Context.for_session(args, session_lock=False)
    node = ctx.resolve_schedule_arg(args.schedule)
    root = _session_root_schedule(ctx.catalog, ctx.session.seed_idea_ids, node)
    if root is None:
        raise DhHlError(
            "session_root_of: no ancestor schedule of {} is a child of a seed "
            "idea of the current session".format(
                ctx.catalog.format_schedule_id(node)))
    print(ctx.catalog.format_schedule_id(root))


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


def cmd_list_output_schedules(args):
    # Read-only over git-tracked state (idea.md marks it session-lock-free).
    ctx = Context.for_session(args, session_lock=False)
    session = ctx.session
    if not session.has_outputs():
        raise DhHlError("the current session has no output schedules yet")
    # Stored order (primary first) -- NOT sorted by timestamp.
    for sid in session.output_schedule_ids:
        _print_schedule_node(ctx, ctx.catalog.get_schedule(sid))


# ---------------------------------------------------------------------------
# json_schedule_info / json_idea_info
# ---------------------------------------------------------------------------

def _schedule_json(catalog, node):
    # cancelled_by is derivable from this node alone (cancels are same-node);
    # convert same-node local IDs to full commentary IDs for the output.
    cancelled_by = node.commentary_cancelled_by()

    def full(local_id):
        return "{}_{}".format(node.full_id, local_id)

    return {
        "id": node.full_id,
        "parent": node.parent_id,
        "children": [i.full_id for i in catalog.child_ideas(node)],
        "source": node.source,
        "parameters": node.parameters,
        "timestamp": node.timestamp,
        "hash": node.hash,
        "result": node.result,
        "review": node.review,
        "benchmark": [b.data for b in node.benchmarks],
        "commentary": [
            {"id": c.full_id,
             "text": c.text,
             "review": c.review,
             "cancels": [full(x) for x in c.cancels],
             "cancelled_by": [full(x) for x in cancelled_by.get(c.local_id, [])]}
            for c in sorted(node.commentary, key=lambda c: c.timestamp)
        ],
        "warning_toggles": [
            {"id": w.full_id,
             "citation": w.citation,
             "func": w.func,
             "rule": w.rule,
             "cancels": w.cancels}
            for w in sorted(node.warning_toggles, key=lambda w: w.timestamp)
        ],
    }


def _idea_json(catalog, idea):
    return {
        "id": idea.full_id,
        "parent": idea.parent_id,
        "children": [s.full_id for s in catalog.child_schedules(idea)],
        "proposal_name": idea.proposal_name,
        "proposal_text": idea.proposal_text,
        "canonical_schedule": idea.canonical,
        "idea_side_links": [
            {"id": dest, "type": link_type}
            for link_type, dest in idea.side_links
        ],
        "review": idea.review,
    }


def _session_json(catalog, session):
    return {
        "id": session.full_id,
        "parent": session.parent_id,
        "children": [c.full_id for c in catalog.child_sessions(session)],
        "prompt": session.prompt,
        "default_anchor_schedule": session.default_anchor_schedule_id,
        "golden_schedule_on_opening": session.golden_schedule_id_on_opening,
        "enabled_problems_on_opening": list(session.enabled_problem_ids_on_opening),
        "seed_ideas": list(session.seed_idea_ids),
        "output_schedules": list(session.output_schedule_ids),
        "output_benchmark_sets": list(session.output_benchmark_set_ids),
        "delisted": session.delisted,
        "depth": session.depth,
    }


def cmd_view_generator_parameters(args):
    ctx = Context.for_catalog(args)
    node = ctx.resolve_schedule_arg(args.schedule)
    # One line per parameters object: "{index} {JSON object as one line}".
    for i, obj in enumerate(node.parameters):
        print("{} {}".format(i, json.dumps(obj, sort_keys=True)))


def cmd_json_schedule_info(args):
    ctx = Context.for_catalog(args)
    node = ctx.resolve_schedule_arg(args.schedule)
    print(json.dumps(_schedule_json(ctx.catalog, node), indent=1))


def cmd_json_benchmark_info(args):
    ctx = Context.for_catalog(args)
    bench = ctx.catalog.resolve_benchmark(args.benchmark)
    print(json.dumps(bench.data, indent=1))


def cmd_json_benchmark_set_info(args):
    ctx = Context.for_catalog(args)
    bs = ctx.catalog.resolve_benchmark_set(args.benchmark_set)
    print(json.dumps(bs.data, indent=1))


def cmd_view_benchmark_stdout(args):
    ctx = Context.for_catalog(args)
    bench = ctx.catalog.resolve_benchmark(args.benchmark)
    # Pre-stdout benchmarks default to "" (idea.md "Benchmark Sub-object State").
    sys.stdout.write(bench.data.get("stdout", ""))


# ---------------------------------------------------------------------------
# cost query tools (json_ranking_cost / json_compare_cost)
# ---------------------------------------------------------------------------

def _resolve_anchor_arg(ctx, spec):
    """Map the `--anchor` argument to an anchor schedule full ID or None
    (idea.md "JSON Ranking Cost Query Tool").  `none` -> no anchor; `always` ->
    the session's current anchor (error if unset); `auto` (default) -> the
    current anchor if set else no anchor; anything else -> an explicit
    schedule ID."""
    spec = spec or "auto"
    if spec == "none":
        return None
    current = ctx.workspace.current_anchor_schedule_id
    if spec == "always":
        if current is None:
            raise DhHlError(
                "--anchor always: the session has no current anchor schedule")
        return current
    if spec == "auto":
        return current
    return ctx.catalog.resolve_schedule(spec).full_id


def _confidence_arg(args):
    """The `--confidence` value (fraction), defaulting to cost.DEFAULT_CONFIDENCE
    and validated to 0 < ci < 1 (idea.md)."""
    ci = getattr(args, "confidence", None)
    if ci is None:
        return cost.DEFAULT_CONFIDENCE
    if not 0.0 < ci < 1.0:
        raise DhHlError("--confidence must satisfy 0 < ci < 1")
    return ci


def _bootstrap_arg(args):
    """The `--bootstrap` resample count, defaulting to cost.DEFAULT_BOOTSTRAP."""
    b = getattr(args, "bootstrap", None)
    if b is None:
        return cost.DEFAULT_BOOTSTRAP
    if b < 2:
        raise DhHlError("--bootstrap must be at least 2")
    return b


def _cost_data(ctx, problem_id=None):
    return cost.CostData.from_private_sets(
        ctx.workspace.read_private_benchmark_sets(), problem_id)


def _cost_problem_id(ctx, spec):
    """The single problem a cost query uses: `--problem` if given, else the main
    problem (idea.md "Cost Comparison Methodology": cost is per one problem,
    default main)."""
    if spec is not None:
        return ctx.catalog.resolve_problem(spec).full_id
    return ctx.catalog.main_problem().full_id


def _warn_no_cost_batches(ctx, private_sets, problem_id, first, second,
                          second_role, suggest_init_tail):
    """Verbose stderr breakdown when a cost query finds 0 batches (idea.md "Cost
    Model Benchmark Search Warnings").  *first* is the target/LHS node; *second*
    is the anchor/RHS node (or None); *second_role* labels it in the breakdown;
    *suggest_init_tail* is the extra init_build flags to suggest ("--other X" /
    "--anchor X" / "").  The breakdown counts are pre-problem-filter, so the user
    can see whether the batches were lost to the schedule filters or the problem
    filter."""
    cat = ctx.catalog
    all_data = cost.CostData.from_private_sets(private_sets)  # unfiltered by problem
    first_short = cat.format_schedule_id(first)
    p_short = cat.format_problem_id(cat.get_problem(problem_id))
    fb = all_data.batches_of(first.full_id)
    lines = ["dh_hl: warning: no benchmark batches for this cost query (cost "
             "reads as null). Reachable batch breakdown:",
             "  by {} alone: {}".format(first_short, len(fb))]
    if second is not None:
        lines.append("  also requiring {} ({}): {}".format(
            cat.format_schedule_id(second), second_role,
            len(fb & all_data.batches_of(second.full_id))))
    lines.append("  also requiring problem {}: 0".format(p_short))
    init = "    dh_hl init_build --target {}".format(first_short)
    if suggest_init_tail:
        init += " " + suggest_init_tail
    lines += ["Profile them together for this problem, e.g.:", init,
              "    dh_hl build --profile ... --problem {}".format(p_short)]
    sys.stderr.write("\n".join(lines) + "\n")


def cmd_json_ranking_cost(args):
    # Reads the private benchmark set list (private workspace) -> session lock.
    ctx = Context.for_session(args, session_lock=True)
    node = ctx.resolve_schedule_arg(args.schedule)
    anchor_spec = getattr(args, "anchor", None)
    anchor_id = _resolve_anchor_arg(ctx, anchor_spec)
    problem_id = _cost_problem_id(ctx, getattr(args, "problem", None))
    private_sets = ctx.workspace.read_private_benchmark_sets()
    r = cost.CostData.from_private_sets(
        private_sets, problem_id).ranking_cost(node.full_id, anchor_id)
    raw = r["raw_costs"]
    out = {
        "batch_count": r["batch_count"],
        "cost": r["cost"],
        "anchor": r["anchor"],
        "representative": r["representative"],
        # One entry per generator parameters object (null where unbenchmarked).
        "parameters_raw_cost": [raw.get(i) for i in range(len(node.parameters))],
    }
    print(json.dumps(out, indent=1))
    if r["batch_count"] == 0:
        anchor_node = ctx.catalog.get_schedule(anchor_id) if anchor_id else None
        tail = ("" if anchor_spec in (None, "auto")
                else "--anchor {}".format(anchor_spec))
        _warn_no_cost_batches(ctx, private_sets, problem_id, node, anchor_node,
                              "anchor", tail)


def cmd_json_compare_cost(args):
    ctx = Context.for_session(args, session_lock=True)
    lhs = ctx.resolve_schedule_arg(getattr(args, "lhs", None))
    rhs_spec = getattr(args, "rhs", None)
    if rhs_spec is not None:
        rhs = ctx.catalog.resolve_schedule(rhs_spec)
    else:
        # Default RHS: the parent schedule of the LHS's parent idea (idea.md).
        if lhs.is_root():
            raise DhHlError(
                "LHS is a root schedule (no parent idea) so the default RHS is "
                "undefined; pass an explicit RHS schedule ID")
        rhs = lhs.parent_idea().parent_schedule()
    confidence, bootstrap = _confidence_arg(args), _bootstrap_arg(args)
    # Once per selected problem (default: all enabled problems), so the output is
    # a list of per-problem comparisons (idea.md "JSON Compare Cost Tool").
    problems = ctx.catalog.select_problems(getattr(args, "problem", None))
    private_sets = ctx.workspace.read_private_benchmark_sets()
    rhs_tail = "--other {}".format(ctx.catalog.format_schedule_id(rhs))
    results = []
    for problem in problems:
        r = cost.CostData.from_private_sets(private_sets, problem.full_id).compare(
            lhs.full_id, rhs.full_id, confidence, bootstrap)
        results.append({"problem": problem.full_id,
                        "problem_short_id": ctx.catalog.format_problem_id(problem),
                        **r})
        if r["batch_count"] == 0:
            _warn_no_cost_batches(ctx, private_sets, problem.full_id, lhs, rhs,
                                  "RHS", rhs_tail)
    if getattr(args, "boolean", False):
        out = {"any_improvement": any(r["result"] == "improvement" for r in results),
               "any_regression": any(r["result"] == "regression" for r in results),
               "any_unknown": any(r["result"] == "unknown" for r in results)}
        print(json.dumps(out, indent=1))
    else:
        print(json.dumps(results, indent=1))


def _reachable_benchmarks_by_param(catalog, private_sets, sched_id,
                                   problem_id=None):
    """``{parameters index: [Benchmark, ...]}`` for benchmarks of *sched_id*
    reachable from the private benchmark set list (idea.md json_profiler_stats).
    Version-mismatched sets are skipped (with a stderr warning), matching the
    cost core -- via the shared cost.compatible_sets gate.  If *problem_id* is
    given, only sets recorded for that problem contribute (a set is
    single-problem)."""
    from collections import defaultdict
    out = defaultdict(list)
    for _set_id, cache in cost.compatible_sets(private_sets):
        if problem_id is not None and cache.get("problem") != problem_id:
            continue
        cells = cache.get("schedules", {}).get(sched_id)
        if not cells:
            continue
        for pidx, cell in enumerate(cells):
            for bid in cell.get("id", []):
                out[pidx].append(catalog.resolve_benchmark(bid))
    return out


def cmd_json_profiler_stats(args):
    ctx = Context.for_session(args, session_lock=True)
    node = ctx.resolve_schedule_arg(args.schedule)
    problem_id = _cost_problem_id(ctx, getattr(args, "problem", None))
    reachable = _reachable_benchmarks_by_param(
        ctx.catalog, ctx.workspace.read_private_benchmark_sets(), node.full_id,
        problem_id)
    if not reachable:
        raise DhHlError(
            "no benchmarks reachable from the private benchmark set list for "
            "this schedule and problem")

    pidx = getattr(args, "parameters", None)
    if pidx is None:
        # Mandatory only when the reachable benchmarks span >1 params object.
        if len(reachable) > 1:
            raise DhHlError(
                "--parameters is required: reachable benchmarks span {} "
                "parameters objects ({})".format(
                    len(reachable), sorted(reachable)))
        pidx = next(iter(reachable))
    elif pidx not in reachable:
        raise DhHlError(
            "no reachable benchmarks for --parameters {}".format(pidx))

    hottest = getattr(args, "hottest", None)
    if hottest is not None and hottest < 1:
        raise DhHlError("--hottest must be >= 1")

    out = profiler_stats.aggregate(
        [b.profiler for b in reachable[pidx]],
        getattr(args, "p", None), getattr(args, "f", None), hottest=hottest)
    print(json.dumps(out, indent=1))


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
        "benchmark_sets": {b.full_id: b.data
                           for b in catalog.benchmark_sets.values()},
        "problems": {p.full_id: _problem_json(p)
                     for p in catalog.problems.values()},
        "goldens": {g.full_id: _golden_json(g)
                    for g in catalog.goldens.values()},
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

def _create_session(catalog, parent_schedules, proposal_name, prompt_text,
                    parent_session, depth, default_anchor_schedule_id=None):
    """Session Creation Common flow (idea.md "Session Creation Tools").  Creates
    one seed idea per parent schedule (all sharing *proposal_name*, so distinct
    IDs), each tagged session.{proposal name} in the PARENT session's private
    list and given a duplicate of its parent schedule as its canonical.  Seeds a
    new session (prompt = *prompt_text*, no workspace init) with those seed ideas
    and *default_anchor_schedule_id*.  Returns (session, handle).

    *parent_schedules* is a non-empty list of ScheduleNode."""
    if not parent_schedules:
        raise DhHlError("session creation needs at least one parent schedule")
    session_id = catalog.mint_session_id(depth)
    text = prompt_text if prompt_text.endswith("\n") else prompt_text + "\n"
    text += "Created for session: {}\n".format(session_id)
    parent_ws = (SessionWorkspace(catalog.catalog_dir, parent_session.full_id,
                                  catalog=catalog)
                 if parent_session is not None else None)
    seed_ideas = []
    for ps in parent_schedules:
        idea = catalog.create_idea(ps, proposal_name, text)
        seed_ideas.append(idea)
        # The seed idea joins the PARENT (current) session's private idea list,
        # tagged session.{proposal name}.  new_catalog has no parent session, so
        # it adds to nothing.
        if parent_ws is not None:
            parent_ws.set_pool_tag(idea.full_id, "session." + proposal_name)
        # Duplicate the parent schedule as the seed idea's canonical, giving the
        # new session an exclusive sub-tree to explore.
        dup = catalog.create_schedule(ps.source, parent_idea=idea,
                                      params_text=ps.params_text)
        idea.set_canonical(dup.full_id)
    # The private workspace is deliberately NOT initialized here (idea.md);
    # the new agent runs `dh_hl init_workspace`.
    session = catalog.create_session(
        seed_ideas, parent_session, depth, prompt=prompt_text,
        default_anchor_schedule_id=default_anchor_schedule_id,
        session_id=session_id)
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
    prompt_text = read_text_or_stdin(args.proposal)
    # Optional generator parameters; default is "[{}]" (benchmark once, no params).
    if getattr(args, "input_parameters", None) is not None:
        params_text = load_parameters_text(read_text_or_stdin(args.input_parameters))
    else:
        params_text = dump_parameters(DEFAULT_PARAMETERS)

    safety.makedirs_tracked(catalog_dir)
    locks.acquire_catalog(catalog_dir)
    catalog = Catalog(catalog_dir)
    catalog.ensure_created()
    root = catalog.create_schedule(input_source, parent_idea=None,
                                   params_text=params_text)
    # The default problem reproduces the harness's historical hard-wired runner
    # (idea.md "New Catalog Tool"): a standalone RunGenMain benchmarking all
    # outputs at their set_estimate sizes.  It is the `main` problem so the cost
    # tools have a well-defined default.  Created BEFORE the session so the first
    # session records it in "enabled problems on opening".
    catalog.create_problem(
        ["<RunGenMain>", "--benchmarks=all", "--estimate_all"],
        "default", state="main")
    # No parent session and no default anchor (a user-provided schedule may be
    # poor, so it's not a safe anchor -- profiling might never terminate).  No
    # golden is added by default, so golden-on-opening is none.
    session, handle = _create_session(
        catalog, [root], args.proposal_name, prompt_text,
        parent_session=None, depth=0)
    catalog.flush()
    safety.commit()
    print("Created catalog " + catalog_dir)
    print("Session: " + session.full_id)
    print("Session handle: " + handle)


def _resolve_schedule_list(ctx, schedule_args):
    """Resolve a `[schedule IDs...]` list; an empty list falls back to the
    default single [schedule ID] (the unambiguous workspace node)."""
    if not schedule_args:
        return [ctx.resolve_schedule_arg(None)]
    return [ctx.catalog.resolve_schedule(s) for s in schedule_args]


def cmd_new_sub_session(args):
    ctx = Context.for_session(args, session_lock=True)
    parents = _resolve_schedule_list(ctx, getattr(args, "schedule", None) or [])
    prompt_text = read_text_or_stdin(args.proposal)
    # The sub-session inherits the current session's *current* anchor (may be none).
    default_anchor = ctx.workspace.current_anchor_schedule_id
    session, handle = _create_session(
        ctx.catalog, parents, args.proposal_name, prompt_text,
        parent_session=ctx.session, depth=ctx.session.depth + 1,
        default_anchor_schedule_id=default_anchor)
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
            "the current session must be self-closed (have outputs or be "
            "delisted) before starting a successor")
    if not session.has_outputs():
        raise DhHlError(
            "the current session has no output schedules to succeed from "
            "(it was only delisted)")
    parents = [ctx.catalog.get_schedule(sid)
               for sid in session.output_schedule_ids]
    prompt_text = read_text_or_stdin(args.proposal)
    # Default anchor = the primary output of the current session.
    new_session, handle = _create_session(
        ctx.catalog, parents, args.proposal_name, prompt_text,
        parent_session=session, depth=0,
        default_anchor_schedule_id=session.primary_output_schedule_id)
    ctx.finish()
    print("Created successor session " + new_session.full_id)
    print("Session handle: " + handle)


# ---- close / delist -------------------------------------------------------

# ---- should_accept checks (shared with close_session) ---------------------

# check kind -> the close_session flag that overrides it (idea.md "Should-accept
# Schedule Tool" / "Close Session Tool").
_ACCEPT_OVERRIDE_FLAGS = {
    "failed_problems": "--allow-failed-problems",
    "failed_golden": "--allow-failed-golden",
    "disabled_problems": "--allow-disabled-problems",
    "changed_golden": "--allow-changed-golden",
}

# check kind -> the argparse attribute set by its override flag (dashes to
# underscores).  Used by close_session to see which failures were force-accepted.
_ACCEPT_OVERRIDE_ATTRS = {
    kind: flag[len("--"):].replace("-", "_")
    for kind, flag in _ACCEPT_OVERRIDE_FLAGS.items()
}


def _hlpipe_path(ctx, schedule_id):
    """Path in this session's bin/ of a schedule's algorithm hlpipe (0th
    generator parameters -- the algorithm is params-independent, and 0 is the
    canonical index new_golden uses)."""
    return os.path.join(
        ctx.workspace.bin_dir,
        build._build_output_rel(schedule_id, "algorithm_hlpipe", 0))


def _missing_problem_benchmarks(ctx, node):
    """(problem, params index) pairs for which NO benchmark encoding (node, that
    params index, that problem) is reachable from the private benchmark set list.
    Each enabled problem x each of the node's parameters objects is required
    (idea.md "Failed Problem Check").  Failed runs emit no benchmark, so presence
    of a benchmark ID is proof the run succeeded."""
    private = ctx.workspace.read_private_benchmark_sets()
    covered = {}  # problem full ID -> set of params indices with >=1 benchmark
    for cache in private.values():
        cells = cache.get("schedules", {}).get(node.full_id)
        if not cells:
            continue
        seen = covered.setdefault(cache.get("problem"), set())
        for pidx, cell in enumerate(cells):
            if cell.get("id"):
                seen.add(pidx)
    missing = []
    for problem in ctx.catalog.enabled_problems():
        have = covered.get(problem.full_id, set())
        for pidx in range(len(node.parameters)):
            if pidx not in have:
                missing.append((problem, pidx))
    return missing


def _failed_golden_message(ctx, node):
    """None if the golden check passes (or there is no golden schedule node),
    else a diagnostic (idea.md "Failed Golden Check")."""
    golden = ctx.catalog.golden_schedule_node()
    if golden is None:
        return None
    nid = ctx.catalog.format_schedule_id(node)
    tpath, gpath = _hlpipe_path(ctx, node.full_id), _hlpipe_path(ctx, golden.full_id)
    if not os.path.isfile(tpath) or not os.path.isfile(gpath):
        return ("failed golden check: the algorithm hlpipe is not built for both "
                "{} and the golden schedule node; build them with `dh_hl "
                "init_build --target {} --other golden && dh_hl build` (the "
                "generator must emit the algorithm hlpipe)".format(nid, nid))
    with open(tpath, "rb") as f:
        target_bytes = f.read()
    with open(gpath, "rb") as f:
        golden_bytes = f.read()
    if target_bytes != golden_bytes:
        return ("failed golden check: {}'s algorithm hlpipe differs from the "
                "golden schedule node -- the algorithm changed".format(nid))
    return None


def _disabled_opening_problems_message(ctx):
    """None if every enabled-on-opening problem is still enabled, else a
    diagnostic (idea.md "Deleted Problem Check")."""
    catalog = ctx.catalog
    bad = []
    for pid in ctx.session.enabled_problem_ids_on_opening:
        p = catalog.problems.get(pid)
        if p is None or not p.is_enabled():
            bad.append(catalog.format_problem_id(p) if p is not None else pid)
    if not bad:
        return None
    return ("deleted problem check: problem(s) enabled when this session opened "
            "are now disabled/gone:\n" + "\n".join("  " + b for b in bad))


def _changed_golden_message(ctx):
    """None if the golden schedule node is unchanged since the session opened,
    else a diagnostic (idea.md "Changed Golden Check")."""
    opening = ctx.session.golden_schedule_id_on_opening
    if opening is None:
        return None
    current = ctx.catalog.golden_schedule_node()
    if current is not None and current.full_id == opening:
        return None
    catalog = ctx.catalog
    was = (catalog.format_schedule_id(catalog.get_schedule(opening))
           if opening in catalog.schedules else opening)
    now = catalog.format_schedule_id(current) if current is not None else "none"
    return ("changed golden check: the golden schedule node changed since this "
            "session opened (was {}, now {})".format(was, now))


def should_accept_failures(ctx, node):
    """The failed suitability checks for *node* as a primary output schedule, as
    an ordered list of (kind, message).  The failed-problem check runs for every
    session; the golden/deleted-problem checks run only for top-level (depth 0)
    sessions (idea.md "Should-accept Schedule Tool").  Shared by should_accept
    and close_session."""
    catalog = ctx.catalog
    failures = []
    missing = _missing_problem_benchmarks(ctx, node)
    if missing:
        lines = ["failed problem check: no benchmark reachable for {} at:".format(
            catalog.format_schedule_id(node))]
        for problem, pidx in missing:
            lines.append("  problem {} (parameters index {})".format(
                catalog.format_problem_id(problem), pidx))
        failures.append(("failed_problems", "\n".join(lines)))
    if ctx.session.depth == 0:
        for kind, msg in (
                ("failed_golden", _failed_golden_message(ctx, node)),
                ("disabled_problems", _disabled_opening_problems_message(ctx)),
                ("changed_golden", _changed_golden_message(ctx))):
            if msg is not None:
                failures.append((kind, msg))
    return failures


def cmd_should_accept(args):
    # Read-only (private benchmark sets + bin/), so no session lock -- like status.
    ctx = Context.for_session(args, session_lock=False)
    node = ctx.resolve_schedule_arg(getattr(args, "schedule", None))
    failures = should_accept_failures(ctx, node)
    print("schedule: " + ctx.catalog.format_schedule_id(node))
    if not failures:
        print("All checks passed; suitable as a primary output schedule.")
        return
    flags = []
    for kind, message in failures:
        print(message)
        flags.append(_ACCEPT_OVERRIDE_FLAGS[kind])
    print("Overrides needed for close_session: " + " ".join(flags))


def cmd_close_session(args):
    ctx = Context.for_session(args, session_lock=True)
    catalog = ctx.catalog
    session = ctx.session
    ws = ctx.workspace
    if session.has_outputs():
        raise DhHlError("the current session already has outputs")

    outputs = _resolve_schedule_list(ctx, getattr(args, "schedule", None) or [])
    # Validate every output and compute its pool tag (from its parent idea's
    # entry in the private idea list) BEFORE mutating anything.
    schedule_pool_pairs = []
    for node in outputs:
        if node.is_root():
            raise DhHlError(
                "an output schedule cannot be a root node: "
                + catalog.format_schedule_id(node))
        if not node.is_major():
            raise DhHlError(
                "output schedule {} is not a major schedule; only the canonical "
                "schedule of an idea can be a session output. Make it canonical "
                "with `dh_hl canon` first".format(catalog.format_schedule_id(node)))
        parent_idea = node.parent_idea()
        if not ws.has_private_idea(parent_idea.full_id):
            raise DhHlError(
                "output {}'s parent idea {} is not in this session's private "
                "idea list; fix with `dh_hl set_pool_tag`".format(
                    catalog.format_schedule_id(node),
                    catalog.format_idea_id(parent_idea)))
        if not node.commentary:
            raise DhHlError(
                "output schedule {} has no commentary; use `dh_hl comment` to "
                "record a session summary first".format(
                    catalog.format_schedule_id(node)))
        schedule_pool_pairs.append(
            (node.full_id, ws.get_pool_tag(parent_idea.full_id)))

    # should_accept suitability checks on the PRIMARY output (idea.md "Close
    # Session Tool"): each failure blocks the close unless its --allow-* override
    # was passed.  Run before any mutation so a blocked close is a clean no-op.
    primary = outputs[0]
    unresolved = [(kind, message)
                  for kind, message in should_accept_failures(ctx, primary)
                  if not getattr(args, _ACCEPT_OVERRIDE_ATTRS[kind], False)]
    if unresolved:
        lines = ["cannot close session: {} failed suitability check(s) on the "
                 "primary output {} (run `dh_hl should_accept` for detail); pass "
                 "the named flag to force anyway:".format(
                     len(unresolved), catalog.format_schedule_id(primary))]
        for kind, message in unresolved:
            lines.append(message)
            lines.append("  override: " + _ACCEPT_OVERRIDE_FLAGS[kind])
        raise DhHlError("\n".join(lines))

    benchmark_sets = list(ws.read_private_benchmark_sets().keys())
    session.set_outputs(schedule_pool_pairs, benchmark_sets)

    # Superseded-by links: for each output O, the session-root schedule R (its
    # ancestor whose parent idea is a seed idea) has its parent idea superseded
    # by O's parent idea.  Silently skipped where session_root_of fails.
    for node in outputs:
        root = _session_root_schedule(catalog, session.seed_idea_ids, node)
        if root is None:
            continue
        root.parent_idea().add_side_link("superseded_by",
                                         node.parent_idea().full_id)

    ctx.finish()
    print("Closed session; {} output schedule(s), primary {}".format(
        len(outputs), catalog.format_schedule_id(outputs[0])))


def cmd_delist_session(args):
    ctx = Context.for_session(args, session_lock=True)
    ctx.session.set_delisted()
    ctx.finish()
    print("Delisted session " + ctx.session.full_id)


# ---- join_session ---------------------------------------------------------

def _resolve_other_session(catalog, spec):
    """Resolve a SECOND session (the joined one) within *catalog*: a handle
    (must point at this catalog) or a session full ID."""
    if spec.startswith("tmp."):
        cat_dir, sid = locks.resolve_handle(spec)
        if os.path.abspath(cat_dir) != catalog.catalog_dir:
            raise DhHlError(
                "joined session handle {} is for a different catalog".format(spec))
        return catalog.get_session(sid)
    if not ids.is_session_id(spec):
        raise DhHlError("not a session handle or valid session ID: " + spec)
    return catalog.get_session(spec)


def cmd_join_session(args):
    # Locks only the CURRENT session (idea.md): the joined session's git-tracked
    # outputs are read under the catalog lock; its private workspace is untouched.
    ctx = Context.for_session(args, session_lock=True)
    catalog = ctx.catalog
    ws = ctx.workspace
    joined = _resolve_other_session(catalog, args.joined)
    if not joined.has_outputs():
        raise DhHlError("the joined session has no outputs to join")
    dry = bool(getattr(args, "dry_run", False))
    prefix = getattr(args, "pool_prefix", "") or ""

    # Snapshot (copy): read_private_ideas is now the live dict, and the
    # set_pool_tag calls below mutate it; we need the pre-join membership.
    existing = dict(ws.read_private_ideas())
    joined_tags = joined.output_schedule_pool_tags()

    for bs_id in joined.output_benchmark_set_ids:
        print("dh_hl: add benchmark set " + bs_id)
        if not dry:
            ws.add_private_benchmark_set(bs_id)

    for sid in joined.output_schedule_ids:
        # An output's parent idea is added to the current private list.  A root
        # output "can't happen" (close_session forbids it); a raw error is OK.
        parent_idea_id = catalog.get_schedule(sid).parent_id
        if parent_idea_id in existing:
            tag = existing[parent_idea_id]                 # unchanged
        else:
            tag = joined_tags[sid]
            if prefix:
                tag = "{}.{}".format(prefix, tag)
        print("dh_hl: add idea " + parent_idea_id)
        print("dh_hl: pool tag " + tag)
        if not dry:
            ws.set_pool_tag(parent_idea_id, tag)

    # --dry-run must have mutated nothing; assert that as a self-check.
    catalog.flush()
    safety.commit(assert_no_writes=dry)


# ---- session private idea list (cost-ranked frontier) ---------------------

def _pool_enable_predicate(pools_exact, pools_regex):
    """Build `enabled(tag) -> bool` from the --pool / --pools arguments.  With no
    such arguments, every pool tag *without a leading `.`* is enabled -- i.e.
    hidden ideas (hide_private_idea prepends `.`) are excluded by default, but an
    explicit --pool/--pools can still name/match a hidden pool (idea.md)."""
    exact = set(pools_exact or [])
    try:
        patterns = [re.compile(p) for p in (pools_regex or [])]
    except re.error as e:
        raise DhHlError("invalid --pools regex: {}".format(e))
    if not exact and not patterns:
        return lambda tag: not tag.startswith(".")
    return lambda tag: tag in exact or any(p.search(tag) for p in patterns)


def _idea_cost_schedule(idea):
    """The schedule whose cost stands in for the idea: its canonical schedule if
    it has one, else its parent schedule (idea.md)."""
    return (idea.canonical if idea.canonical is not None
            else idea.parent_schedule().full_id)


def _obsoleted_by(ctx, data, idea, confidence):
    """Child ideas of *idea*'s canonical schedule that confidently improve on it
    (idea.md "Obsoleted By").  Empty unless *idea* has a canonical schedule."""
    if idea.canonical is None:
        return []
    canon_node = ctx.catalog.schedules.get(idea.canonical)
    if canon_node is None:
        return []
    out = []
    for child in ctx.catalog.child_ideas(canon_node):
        if child.canonical is None:
            continue
        if data.is_improvement(child.canonical, idea.canonical, confidence,
                               cost.DEFAULT_BOOTSTRAP):
            out.append(child)
    return out


def cmd_list_private_ideas(args):
    ctx = Context.for_session(args, session_lock=True)
    catalog = ctx.catalog
    ws = ctx.workspace
    anchor_id = _resolve_anchor_arg(ctx, getattr(args, "anchor", None))
    confidence = _confidence_arg(args)
    max_n = getattr(args, "max", None)
    max_n = 6 if max_n is None else max_n
    done = bool(getattr(args, "done", False))
    todo = bool(getattr(args, "todo", False))
    enabled = _pool_enable_predicate(getattr(args, "pool", None),
                                     getattr(args, "pools", None))
    problem_id = _cost_problem_id(ctx, getattr(args, "problem", None))
    data = cost.CostData.from_private_sets(
        ws.read_private_benchmark_sets(), problem_id)

    # Group the enabled private ideas by pool tag, and cost each once.
    by_pool = {}
    cost_of = {}   # idea full id -> ranking_cost dict
    for idea_id, tag in ws.read_private_ideas().items():
        if not enabled(tag):
            continue
        idea = catalog.ideas.get(idea_id)
        if idea is None:
            continue  # dangling (e.g. git checkout desync); skip defensively
        by_pool.setdefault(tag, []).append(idea)
        cost_of[idea_id] = data.ranking_cost(_idea_cost_schedule(idea), anchor_id)

    any_low_cost = False
    for tag in sorted(by_pool):
        print("=== {} ===".format(tag))
        # Sort by cost (a null cost sorts as 0 -> bubbles to the top); then apply
        # the --done/--todo filter; then truncate to --max.
        ideas = sorted(by_pool[tag],
                       key=lambda i: cost_of[i.full_id]["cost"] or 0)
        if done:
            ideas = [i for i in ideas if i.canonical is not None]
        elif todo:
            ideas = [i for i in ideas if i.canonical is None]
        for idea in ideas[:max_n]:
            rc = cost_of[idea.full_id]
            _print_idea_listing(ctx, idea)
            print("  batch_count: {}".format(rc["batch_count"]))
            print("  cost: {}".format("null" if rc["cost"] is None else rc["cost"]))
            if (anchor_id is not None and rc["cost"] is not None
                    and rc["cost"] < 0.5):
                any_low_cost = True
            for child in _obsoleted_by(ctx, data, idea, confidence):
                print("  obsoleted by: " + catalog.format_idea_id(child))

    # Drift warnings (idea.md implementation notes).
    if anchor_id is None:
        print("Warning: ranking is drift-exposed until you set an anchor.")
    elif any_low_cost:
        print("Warning: some ranked schedules were much faster than the anchor.")
        print("This amplifies the effects of system noise; consider a new anchor.")


# ---- session idea pool tags -----------------------------------------------

def cmd_get_pool_tag(args):
    # Read-only relative to the git-tracked catalog, but reads the private idea
    # list, so it takes the session lock like the other private-state tools.
    ctx = Context.for_session(args, session_lock=True)
    idea = ctx.catalog.resolve_idea(args.idea)
    print(ctx.workspace.get_pool_tag(idea.full_id))


def cmd_set_pool_tag(args):
    ctx = Context.for_session(args, session_lock=True)
    idea = ctx.catalog.resolve_idea(args.idea)
    ctx.workspace.set_pool_tag(idea.full_id, args.pool_tag)
    ctx.finish()
    print("Set pool tag of {} to {}".format(
        ctx.catalog.format_idea_id(idea), args.pool_tag))


def cmd_hide_private_idea(args):
    ctx = Context.for_session(args, session_lock=True)
    idea = ctx.catalog.resolve_idea(args.idea)
    ctx.workspace.hide_private_idea(idea.full_id)
    ctx.finish()
    print("Hid {} (pool tag now {})".format(
        ctx.catalog.format_idea_id(idea),
        ctx.workspace.get_pool_tag(idea.full_id)))


def cmd_rename_pool_tag(args):
    ctx = Context.for_session(args, session_lock=True)
    n = ctx.workspace.rename_pool_tag(args.pool_tag_before, args.pool_tag_after)
    ctx.finish()
    print("{} idea nodes updated".format(n))


# ---- session private benchmark set list -----------------------------------

def cmd_add_private_benchmark_set(args):
    ctx = Context.for_session(args, session_lock=True)
    ws = ctx.workspace
    added = []
    for spec in (getattr(args, "benchmark_sets", None) or []):
        bs = ctx.catalog.resolve_benchmark_set(spec)  # must exist in the catalog
        ws.add_private_benchmark_set(bs.full_id, ctx.catalog)
        added.append(bs.full_id)
    ctx.finish()
    for sid in added:
        print("Added benchmark set " + sid)


def cmd_remove_private_benchmark_set(args):
    ctx = Context.for_session(args, session_lock=True)
    ws = ctx.workspace
    removed = []
    for spec in (getattr(args, "benchmark_sets", None) or []):
        # Benchmark sets have no short IDs; match the given full ID against the
        # private list directly (tolerating an entry no longer in the catalog).
        if spec in ws.read_private_benchmark_sets():
            ws.remove_private_benchmark_set(spec)
            removed.append(spec)
    ctx.finish()
    for sid in removed:
        print("Removed benchmark set " + sid)


def cmd_list_private_benchmark_sets(args):
    # Reads the private workspace list -> session lock (idea.md).
    ctx = Context.for_session(args, session_lock=True)
    for sid in sorted(ctx.workspace.read_private_benchmark_sets()):
        print(sid)


# ---- init_workspace + current anchor --------------------------------------

_INIT_WS_ALREADY_DEPTH0 = """\
AGENTS: the session seems to already be initialized,
as if in use by (or previously used by) another agent.
Things will fail badly if this session is used concurrently.
If you can speak with the user interactively, ask for a decision:

1. the user finds the conversation that was for this session
and asks that agent to close the session (preferred)

2. inspect the current session workspace and try to pick up
where the previous agent left off.

3. restart the session from scratch (re-run this tool with --force)

If you can't ask (e.g. automated workflow),
don't continue, unless other prompting provides an expected fix."""

_INIT_WS_ALREADY_SUB = """\
AGENTS: the session seems to already be initialized,
as if it's in use by (or previously used by) another agent.
STOP IMMEDIATELY and report to the main agent or user what happened.
You can do so normally, not via `dh_hl close_session`."""


def cmd_init_workspace(args):
    ctx = Context.for_session(args, session_lock=True)
    catalog = ctx.catalog
    session = ctx.session
    ws = ctx.workspace
    ws.ensure_private_dir()
    allow = getattr(args, "force", False)

    # As if `restore_idea` on the 0th seed idea: workspace from the seed idea's
    # parent schedule, current idea state = that seed idea (idea.md).
    seed0 = catalog.get_idea(session.seed_idea_ids[0])
    parent = seed0.parent_schedule()
    # current_idea_state is written directly (bypassing CurrentIdeaState.set_idea)
    # so the --force flag threads through as write_allowed(allow=...).
    idea_state_text = "dendritic_hl_idea({})\n".format(seed0.full_id)
    # Private idea list: every seed idea at pool tag "default".
    private_ideas = {sid: "default" for sid in session.seed_idea_ids}

    anchor = session.default_anchor_schedule_id
    anchor_text = (anchor + "\n") if anchor else ""
    # init_workspace is a pure initializer: it writes each workspace file
    # directly with the --force `allow` flag (so an existing file raises
    # FileExistsError here, caught below), bypassing the lazy state objects.
    try:
        safety.write_allowed(ws.workspace_path, parent.source, allow=allow)
        safety.write_allowed(ws.params_path, parent.params_text, allow=allow)
        safety.write_allowed(ws.current_idea_state.path, idea_state_text,
                             allow=allow)
        safety.write_allowed(ws.current_anchor_path, anchor_text, allow=allow)
        safety.write_allowed(ws.private_ideas_path,
                             json.dumps(private_ideas, indent=1) + "\n",
                             allow=allow)
        safety.write_allowed(ws.private_benchmark_sets_path, "{}\n", allow=allow)
    except FileExistsError:
        # Some workspace state already exists and --force was not given.
        print(_INIT_WS_ALREADY_DEPTH0 if session.depth == 0
              else _INIT_WS_ALREADY_SUB)
        raise DhHlError(
            "session workspace already initialized (see message above; "
            "re-run with --force to reinitialize)")

    ctx.finish()
    print("Initialized workspace for session " + session.full_id)
    print("Current idea: " + catalog.format_idea_id(seed0))


def cmd_get_current_anchor(args):
    ctx = Context.for_session(args, session_lock=True)
    anchor = ctx.workspace.current_anchor_schedule_id
    if anchor is None:
        print("none")
    else:
        print(ctx.catalog.format_schedule_id(ctx.catalog.get_schedule(anchor)))


def cmd_set_current_anchor(args):
    ctx = Context.for_session(args, session_lock=True)
    ws = ctx.workspace
    ws.ensure_private_dir()
    spec = getattr(args, "schedule", None)
    if spec == "none":
        ws.set_current_anchor(None)
        ctx.finish()
        print("Cleared current anchor")
        return
    node = ctx.resolve_schedule_arg(spec)
    ws.set_current_anchor(node.full_id)
    ctx.finish()
    print("Set current anchor to " + ctx.catalog.format_schedule_id(node))


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


def cmd_copy_seed_schedule(args):
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


def cmd_workspace_parameters(args):
    ctx = Context.for_session(args, session_lock=False)
    ctx.session  # validate the session exists
    print(ctx.workspace.params_path)


def cmd_workspace_bin(args):
    ctx = Context.for_session(args, session_lock=False)
    ctx.session
    print(ctx.workspace.bin_dir)


# ---- catalog location -----------------------------------------------------

def cmd_catalog_location(args):
    """Print the catalog directory path.  Non-trivial when -s is a session
    handle, whose file encodes the catalog dir; resolve_target does that."""
    catalog_dir, _ = resolve_target(args)
    print(catalog_dir)


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

def _print_seed_ideas(ctx):
    seeds = ctx.session.seed_idea_ids
    if not seeds:
        print("(no seed ideas)")
    for sid in seeds:
        _print_idea_listing(ctx, ctx.catalog.get_idea(sid),
                            include_proposal_text=False)


def cmd_view_session_prompt(args):
    ctx = Context.for_session(args, session_lock=False)
    # The prompt text, then the seed-idea listing under a divider.
    sys.stdout.write(ctx.session.prompt)
    if not ctx.session.prompt.endswith("\n"):
        print()
    print("=== Seed Ideas ===")
    _print_seed_ideas(ctx)


def _format_cancel_id(catalog, node, target_local):
    """Short commentary ID for a cancels-list entry (same-node local ID); falls
    back to the reconstructed full ID if the target no longer resolves."""
    for other in node.commentary:
        if other.local_id == target_local:
            return catalog.format_commentary_id(other)
    return "{}_{}".format(node.full_id, target_local)


def _print_one_commentary(catalog, c, brief):
    """Print a single commentary sub-object (idea.md "View Commentary Tool").
    `cancelled` is derivable from the parent node alone (cancels are same-node)."""
    node = c.schedule
    cancelled_by = node.commentary_cancelled_by()
    print("=" * 72)
    print("timestamp: " + c.timestamp)
    print("review: " + c.review)
    print("cancelled: " + ("true" if cancelled_by.get(c.local_id) else "false"))
    for target_local in c.cancels:
        print("cancels: " + _format_cancel_id(catalog, node, target_local))
    if brief:
        print(_first_line_72(c.text))
    else:
        print("-" * 72)
        print(c.text.rstrip("\n"))


def _print_all_commentary(catalog, node, brief):
    comments = sorted(node.commentary, key=lambda c: c.timestamp)
    if not comments:
        print("(no commentary)")
    for c in comments:
        _print_one_commentary(catalog, c, brief)


def cmd_view_commentary(args):
    ctx = Context.for_catalog(args)
    c = ctx.catalog.resolve_commentary(args.commentary)
    _print_one_commentary(ctx.catalog, c, brief=bool(getattr(args, "brief", False)))


def cmd_view_all_commentary(args):
    ctx = Context.for_catalog(args)
    _print_all_commentary(ctx.catalog, ctx.resolve_schedule_arg(args.schedule),
                          brief=bool(getattr(args, "brief", False)))


def cmd_view_session_commentary(args):
    ctx = Context.for_session(args, session_lock=False)
    session = ctx.session
    if not session.has_outputs():
        raise DhHlError("the current session has no output schedules yet")
    brief = bool(getattr(args, "brief", False))
    for sid in session.output_schedule_ids:
        node = ctx.catalog.get_schedule(sid)
        print("#" * 72)
        print("# OUTPUT SCHEDULE: " + ctx.catalog.format_schedule_id(node))
        print("#" * 72)
        _print_all_commentary(ctx.catalog, node, brief=brief)


# ---------------------------------------------------------------------------
# warning toggles: add / debug / view benchmark warnings
# ---------------------------------------------------------------------------

def _sorted_toggles(toggles):
    """Deterministic display order for a path-collected toggle list: nearest node
    first (as returned by warning_toggle_state), and by timestamp within a node.
    warning_toggle_state already concatenates in node order, so a stable sort on
    timestamp within each node's block keeps that; we sort the whole list by
    (implicit path index, timestamp) by relying on the input already being in
    path order and doing a stable timestamp sort per contiguous run is overkill --
    a single stable sort on timestamp is enough for readable, reproducible tests
    since timestamps are globally unique."""
    return sorted(toggles, key=lambda w: w.timestamp)


def cmd_add_warning_toggle(args):
    ctx = Context.for_catalog(args)
    catalog = ctx.catalog
    node = catalog.resolve_schedule(args.schedule)
    citation = catalog.resolve_commentary(args.commentary).full_id

    block = getattr(args, "block", None)
    cancel = getattr(args, "cancel", None)
    if (block is None) == (cancel is None):
        raise DhHlError(
            "add_warning_toggle needs exactly one of --block RULE FUNC or "
            "--cancel WARNING_TOGGLE_ID (a WarningToggle either blocks a warning "
            "or cancels another, never both)")

    if block is not None:
        rule, func = block
        w = node.add_warning_toggle(citation, rule=rule, func=func)
    else:
        target = catalog.resolve_warning_toggle(cancel)
        w = node.add_warning_toggle(citation, cancels=target.full_id)

    ctx.finish()
    print("Added WarningToggle {} to {}".format(
        catalog.format_warning_toggle_id(w), catalog.format_schedule_id(node)))


def cmd_debug_warning_toggle(args):
    ctx = Context.for_catalog(args)
    catalog = ctx.catalog
    node = ctx.resolve_schedule_arg(args.schedule)
    toggles, cancelled_ids = catalog.warning_toggle_state(node)

    block = getattr(args, "block", None)
    cancel = getattr(args, "cancel", None)
    # --cancel filter: keep toggles whose cancel-target is the named WarningToggle.
    # "Not an error if the named object does not exist", so resolve leniently and
    # fall back to a literal full ID.  A sentinel (never equal to any real
    # `cancels`, which is None or a full ID) makes an unresolvable target match
    # nothing -- distinct from None, which real block toggles carry.
    _NO_MATCH = object()
    cancel_target = None
    if cancel is not None:
        try:
            cancel_target = catalog.resolve_warning_toggle(cancel).full_id
        except DhHlError:
            cancel_target = cancel if ids.is_warning_toggle_id(cancel) else _NO_MATCH

    out = []
    for w in _sorted_toggles(toggles):
        if block is not None:
            rule, func = block
            if not (w.is_block() and w.rule == rule and w.func == func):
                continue
        if cancel is not None:
            if w.cancels != cancel_target:
                continue
        out.append(w)

    first = True
    for w in out:
        if not first:
            print("-" * 72)
        first = False
        print("id: " + catalog.format_warning_toggle_id(w))
        _print_citation_lines(catalog, w.citation)
        if w.is_block():
            print("rule/func: {} {}".format(w.rule, w.func))
        else:
            target = _format_toggle_ref(catalog, w.cancels)
            print("cancels: " + target)
        print("cancelled: " + ("true" if w.full_id in cancelled_ids else "false"))


def cmd_view_benchmark_warnings(args):
    ctx = Context.for_catalog(args)
    catalog = ctx.catalog
    bench = catalog.resolve_benchmark(args.benchmark)
    node = bench.schedule
    always = bool(getattr(args, "always_show_message", False))

    first = True
    for warning in bench.warnings:
        if not first:
            print("-" * 72)
        first = False
        rule, func = profiler_warnings.warning_key(warning)
        print("rule/func: {} {}".format(rule, func))
        blocker = catalog.blocking_toggle(node, rule, func)
        if blocker is None or always:
            print("message: " + str(profiler_warnings.warning_message(warning)))
        if blocker is not None:
            print("blocked by: " + catalog.format_warning_toggle_id(blocker))
            _print_citation_lines(catalog, blocker.citation)


def _format_toggle_ref(catalog, full_id):
    """A short WarningToggle ID for display, tolerating a dangling reference (the
    target may live on a node not currently loadable / already gone)."""
    if full_id is None:
        return "(none)"
    try:
        return catalog.format_warning_toggle_id(
            catalog.resolve_warning_toggle(full_id))
    except DhHlError:
        return full_id


def _print_citation_lines(catalog, citation_full_id):
    """Print the `citation:` line plus the first-line snippet of the cited
    commentary.  The citation may point anywhere in the catalog; degrade to the
    raw ID if it can no longer be resolved."""
    try:
        c = catalog.resolve_commentary(citation_full_id)
    except DhHlError:
        print("citation: " + str(citation_full_id))
        return
    print("citation: " + catalog.format_commentary_id(c))
    print(_first_line_72(c.text))


# ---- prompt ---------------------------------------------------------------

def cmd_prompt(args):
    """Emit the assembled agent prompt.  The audience is given explicitly and is
    NEVER inferred from the session, so the prompt can serve as an independent
    double-check of the agent's role (main vs sub).  Needs no catalog/session."""
    if bool(args.main) == bool(args.sub):
        raise DhHlError("prompt requires exactly one of --main / --sub")
    sys.stdout.write(prompts.load_prompt("main" if args.main else "sub"))


def cmd_detail(args):
    """Print a supplemental document from the harness source `detail/` dir."""
    sys.stdout.write(prompts.load_doc("detail", args.name))


def cmd_examples(args):
    """Print an example file from the harness source `examples/` dir."""
    sys.stdout.write(prompts.load_doc("examples", args.name))


# ---------------------------------------------------------------------------
# Golden object tools (idea.md "Golden Object Tools")
# ---------------------------------------------------------------------------

def _golden_json(g):
    """json_golden_info format (idea.md): remarks / schedule (null or full ID).
    The golden's stored data dict is already in exactly this shape."""
    return {"remarks": g.remarks, "schedule": g.schedule_id}


def cmd_new_golden(args):
    # Creates a git-tracked golden object AND reads the session's private bin/
    # (the algorithm-hlpipe satisfiability check), so it takes the session lock.
    ctx = Context.for_session(args, session_lock=True)
    catalog = ctx.catalog
    remarks = _read_file_or_stdin(args.remarks)
    spec = getattr(args, "schedule", None)
    if spec == "none":
        schedule_id = None
    else:
        node = ctx.resolve_schedule_arg(spec)
        # A golden schedule must be *satisfiable*: its algorithm hlpipe (from the
        # 0th generator parameters object) must already be built in THIS session,
        # otherwise no future golden check could ever pass against it (idea.md
        # "New Golden Tool").
        src = os.path.join(
            ctx.workspace.bin_dir,
            build._build_output_rel(node.full_id, "algorithm_hlpipe", 0))
        if not os.path.isfile(src):
            sid = catalog.format_schedule_id(node)
            raise DhHlError(
                "no algorithm hlpipe built for {} (0th generator parameters) in "
                "this session; build it first with `dh_hl init_build --target {} "
                "&& dh_hl build`, and ensure the generator emits the algorithm "
                "hlpipe (see `dh_hl help new_golden`)".format(sid, sid))
        schedule_id = node.full_id
    g = catalog.create_golden(remarks, schedule_id)
    ctx.finish()
    print(g.full_id)


def cmd_golden_history(args):
    ctx = Context.for_catalog(args)
    catalog = ctx.catalog
    for g in catalog.goldens_newest_first():
        print("=" * 72)
        print("timestamp: " + g.timestamp)
        if g.schedule_id is None:
            print("schedule: none")
        else:
            print("schedule: " + catalog.format_schedule_id(
                catalog.get_schedule(g.schedule_id)))
        # Remarks are stored verbatim; end the block on a newline either way.
        print(g.remarks, end="" if g.remarks.endswith("\n") else "\n")


def cmd_json_golden_info(args):
    ctx = Context.for_catalog(args)
    print(json.dumps(_golden_json(ctx.catalog.get_golden(args.golden)),
                     indent=1))


# ---------------------------------------------------------------------------
# Problem object tools (idea.md "Problem Object Tools")
# ---------------------------------------------------------------------------

def _problem_json(p):
    """json_problem_info format (idea.md): argv / state / short_name."""
    return {"argv": p.argv, "state": p.state, "short_name": p.short_name}


def cmd_new_problem(args):
    ctx = Context.for_catalog(args)
    p = ctx.catalog.create_problem(list(args.argv or []), args.short_name)
    ctx.finish()
    print(ctx.catalog.format_problem_id(p))


def cmd_disable_problem(args):
    ctx = Context.for_catalog(args)
    ctx.catalog.resolve_problem(args.problem).set_state("disabled")
    ctx.finish()


def cmd_enable_problem(args):
    ctx = Context.for_catalog(args)
    p = ctx.catalog.resolve_problem(args.problem)
    # Enabling a `main` problem leaves it `main` (idea.md "Problem State Tools").
    if p.state != "main":
        p.set_state("enabled")
    ctx.finish()


def cmd_set_main_problem(args):
    ctx = Context.for_catalog(args)
    p = ctx.catalog.resolve_problem(args.problem)
    # Demote any other current main to `enabled`, then promote this one.
    for other in ctx.catalog.problems.values():
        if other.full_id != p.full_id and other.state == "main":
            other.set_state("enabled")
    p.set_state("main")
    ctx.finish()


def cmd_get_problem_short_name(args):
    ctx = Context.for_catalog(args)
    print(ctx.catalog.resolve_problem(args.problem).short_name)


def cmd_set_problem_short_name(args):
    ctx = Context.for_catalog(args)
    ctx.catalog.resolve_problem(args.problem).set_short_name(args.short_name)
    ctx.finish()


def _print_problem(catalog, p):
    """One problem's four-line listing (idea.md "List Problems Tool")."""
    print("=" * 72)
    print("id: " + catalog.format_problem_id(p))
    print("state: " + p.state)
    print("short name: " + p.short_name)
    print("cli: " + json.dumps(p.argv))


def cmd_list_enabled_problems(args):
    ctx = Context.for_catalog(args)
    for p in ctx.catalog.enabled_problems():
        _print_problem(ctx.catalog, p)


def cmd_list_all_problems(args):
    ctx = Context.for_catalog(args)
    for p in ctx.catalog.problems.values():
        _print_problem(ctx.catalog, p)


def cmd_json_problem_info(args):
    ctx = Context.for_catalog(args)
    print(json.dumps(_problem_json(ctx.catalog.resolve_problem(args.problem)),
                     indent=1))


def cmd_problem_full_id(args):
    ctx = Context.for_catalog(args)
    print(ctx.catalog.resolve_problem(args.problem).full_id)


def cmd_problem_short_id(args):
    ctx = Context.for_catalog(args)
    print(ctx.catalog.format_problem_id(
        ctx.catalog.resolve_problem(args.problem)))
