"""The cost-ranked `list_private_ideas` frontier (idea.md "List Session Private
Ideas Tool").

Deterministic: synthetic benchmark sets give each idea's schedule a known cost.
Assertions are *structural* -- the output is parsed into per-idea blocks
(`_parse`) so we check that a SPECIFIC idea has a specific cost / pool / batch
count / obsoleted-by line, not merely that the string appears somewhere (which
would miss a mis-attribution bug)."""

from collections import defaultdict

from dendritic_hl_lib import locks, safety, tools
from dendritic_hl_lib.enums import Result
from dendritic_hl_lib.context import SessionWorkspace
from conftest import add_synthetic_benchmark_set, open_catalog


# ---- output parser (the core of the methodology) --------------------------

def _parse(out):
    """Parse frontier output into (blocks, order, warnings).

    blocks[name] = {"pool", "cost" (float|None), "batch_count" (int),
                    "obsoleted_by": [idea id strings]}
    order[pool]  = [proposal name, ... in printed order]
    warnings     = [warning lines]

    Keyed by proposal name (unique per test).  An idea block is a non-indented
    header line followed by its 2-space-indented fields."""
    blocks, order, warnings = {}, defaultdict(list), []
    pool, cur = None, None
    for line in out.splitlines():
        if line.startswith("=== ") and line.endswith(" ==="):
            pool = line[4:-4]
        elif line.startswith("Warning") or line.startswith("This amplifies"):
            warnings.append(line)   # incl. the amplify warning's 2nd line
        elif line.startswith("  proposal: "):
            cur["name"] = line[len("  proposal: "):]
            cur["pool"] = pool
            blocks[cur["name"]] = cur
            order[pool].append(cur["name"])
        elif line.startswith("  batch_count: "):
            cur["batch_count"] = int(line.split(": ", 1)[1])
        elif line.startswith("  cost: "):
            v = line.split(": ", 1)[1]
            cur["cost"] = None if v == "null" else float(v)
        elif line.startswith("  obsoleted by: "):
            cur["obsoleted_by"].append(line.split(": ", 1)[1])
        elif line and not line.startswith("  "):
            cur = {"obsoleted_by": []}   # a new idea header line
    return blocks, order, warnings


def _out(run_tool, capsys, session, **ns_over):
    capsys.readouterr()
    run_tool(tools.cmd_list_private_ideas, _ns(session, **ns_over))
    return _parse(capsys.readouterr().out)


def _ns(session, **kw):
    for k in ("anchor",):
        kw.setdefault(k, "auto")
    for k in ("confidence", "max", "pool", "pools"):
        kw.setdefault(k, None)
    for k in ("done", "todo"):
        kw.setdefault(k, False)
    return session.ns(**kw)


# ---- catalog setup helpers ------------------------------------------------

def _build(session, fn):
    cat = open_catalog(session.catalog_dir)
    try:
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        out = fn(cat, ws)
        cat.flush(); safety.commit()
        return out
    finally:
        locks._reset_for_tests()


def _seed_canonical(cat, session):
    return cat.get_idea(cat.get_session(session.session_id).seed_idea_id).canonical


def _idea(cat, ws, parent_sched_id, name, pool, cost_batches=None):
    """Create idea *name* (pool tag *pool*) under a major schedule; if
    *cost_batches* is given, also a canonical schedule (to be benchmarked at
    those per-batch costs).  Returns (idea_full_id, canonical_full_id | None)."""
    idea = cat.create_idea(cat.get_schedule(parent_sched_id), name,
                           name + " proposal\n")
    canon = None
    if cost_batches is not None:
        dup = cat.create_schedule(name + " src\n", parent_idea=idea)
        dup.set_result(Result.SUCCESS)
        idea.set_canonical(dup.full_id)
        canon = dup.full_id
    ws.set_pool_tag(idea.full_id, pool)
    return idea.full_id, canon


def _short_idea_id(session, idea_full_id):
    cat = open_catalog(session.catalog_dir)
    try:
        return cat.format_idea_id(cat.get_idea(idea_full_id))
    finally:
        locks._reset_for_tests()


# ---- ranking + grouping ---------------------------------------------------

def test_grouping_and_cost_attribution(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _, ca1 = _idea(cat, ws, C0, "Aexpensive", "a", [200, 201, 199])
        _, ca2 = _idea(cat, ws, C0, "Acheap", "a", [150, 149, 151])
        _, cb1 = _idea(cat, ws, C0, "Bonly", "b", [300, 299, 301])
        set_id = add_synthetic_benchmark_set(
            cat, {ca1: [[200, 201, 199]], ca2: [[150, 149, 151]],
                  cb1: [[300, 299, 301]]})
        ws.add_private_benchmark_set(set_id, cat)
    _build(session, build)

    blocks, order, warnings = _out(run_tool, capsys, session, anchor="none")
    # Each idea is under the RIGHT pool with the RIGHT cost (structural, not a
    # loose "150 appears somewhere").
    assert blocks["Acheap"]["pool"] == "a" and blocks["Acheap"]["cost"] == 150
    assert blocks["Aexpensive"]["pool"] == "a" and blocks["Aexpensive"]["cost"] == 200
    assert blocks["Bonly"]["pool"] == "b" and blocks["Bonly"]["cost"] == 300
    assert blocks["Acheap"]["batch_count"] == 3
    # The seed idea sits in "default" with no benchmarks -> null cost.
    assert blocks["seed"]["pool"] == "default" and blocks["seed"]["cost"] is None
    # Within pool a, cheaper first; pools iterate in sorted order.
    assert order["a"] == ["Acheap", "Aexpensive"]
    assert order["b"] == ["Bonly"]
    # No anchor -> exactly the drift warning.
    assert warnings == ["Warning: ranking is drift-exposed until you set an anchor."]


def test_null_cost_bubbles_to_top(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _, cben = _idea(cat, ws, C0, "Benched", "p", [200, 201, 199])
        _idea(cat, ws, C0, "Unbenched", "p", [])   # canonical, not benchmarked
        set_id = add_synthetic_benchmark_set(cat, {cben: [[200, 201, 199]]})
        ws.add_private_benchmark_set(set_id, cat)
    _build(session, build)
    blocks, order, _ = _out(run_tool, capsys, session, anchor="none", pool=["p"])
    assert order["p"] == ["Unbenched", "Benched"]   # null (=0) sorts first
    assert blocks["Unbenched"]["cost"] is None and blocks["Unbenched"]["batch_count"] == 0


# ---- filters --------------------------------------------------------------

def test_todo_done_filters(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _idea(cat, ws, C0, "HasCanon", "p", [100, 101, 99])
        _idea(cat, ws, C0, "NoCanon", "p", None)   # no canonical schedule
    _build(session, build)

    done, _, _ = _out(run_tool, capsys, session, done=True, pool=["p"])
    assert "HasCanon" in done and "NoCanon" not in done
    todo, _, _ = _out(run_tool, capsys, session, todo=True, pool=["p"])
    assert "NoCanon" in todo and "HasCanon" not in todo


def test_max_truncates_the_cheapest_per_pool(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        # Costs 103,102,101,100 -> Idea3 cheapest, Idea0 dearest.
        specs = {}
        for i in range(4):
            c = 103 - i
            _, canon = _idea(cat, ws, C0, "Idea{}".format(i), "p", [c, c, c])
            specs[canon] = [[c, c, c]]
        set_id = add_synthetic_benchmark_set(cat, specs)
        ws.add_private_benchmark_set(set_id, cat)
    _build(session, build)
    _, order, _ = _out(run_tool, capsys, session, pool=["p"], max=2)
    # Exactly the two cheapest, in cost order.
    assert order["p"] == ["Idea3", "Idea2"]


def test_pool_and_pools_filters(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _idea(cat, ws, C0, "InVec", "vec", [100, 100, 100])
        _idea(cat, ws, C0, "InTile", "tile", [100, 100, 100])
        _idea(cat, ws, C0, "InOther", "misc", [100, 100, 100])
    _build(session, build)

    only_vec, _, _ = _out(run_tool, capsys, session, pool=["vec"])
    assert set(only_vec) == {"InVec"}          # exactly the vec pool
    rx, _, _ = _out(run_tool, capsys, session, pools=["vec|tile"])
    assert set(rx) == {"InVec", "InTile"}      # regex union, misc/default excluded


def test_hidden_pools_excluded_by_default_but_selectable(session, run_tool, capsys):
    """A pool tag with a leading '.' (hide_private_idea) is excluded from the
    default (no --pool/--pools) view, but an explicit --pool/--pools still
    matches it (idea.md leading-'.' convention)."""
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _idea(cat, ws, C0, "Shown", "vis", [100, 100, 100])
        _idea(cat, ws, C0, "Hidden", ".vis", [100, 100, 100])
    _build(session, build)

    # Default: the hidden pool (and its idea) is omitted; "vis" and the seed's
    # "default" remain.
    default_view, _, _ = _out(run_tool, capsys, session)
    assert "Shown" in default_view and "Hidden" not in default_view
    # Explicit --pool names the hidden pool -> it appears.
    picked, _, _ = _out(run_tool, capsys, session, pool=[".vis"])
    assert set(picked) == {"Hidden"}
    # A regex that matches the hidden tag also surfaces it.
    rx, _, _ = _out(run_tool, capsys, session, pools=[r"\.vis"])
    assert "Hidden" in rx


# ---- obsoleted-by ---------------------------------------------------------

def _obsoletion_setup(session, child_cost):
    """Parent(canonical CP, cost 200) with a child idea (canonical CC at
    *child_cost*), both benchmarked in one set.  Returns the child's short id."""
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _, cp = _idea(cat, ws, C0, "Parent", "a", [200, 201, 199, 200, 202])
        child = cat.create_idea(cat.get_schedule(cp), "Child", "child\n")
        cc = cat.create_schedule("child src\n", parent_idea=child)
        cc.set_result(Result.SUCCESS)
        child.set_canonical(cc.full_id)
        ws.set_pool_tag(child.full_id, "a")
        child_batches = [child_cost + d for d in (0, 1, -1, 0, -2)]
        set_id = add_synthetic_benchmark_set(
            cat, {cp: [[200, 201, 199, 200, 202]],
                  cc.full_id: [child_batches]})
        ws.add_private_benchmark_set(set_id, cat)
        return child.full_id
    return _build(session, build)


def test_obsoleted_by_when_child_is_cheaper(session, run_tool, capsys):
    child_id = _obsoletion_setup(session, child_cost=100)   # clearly cheaper
    child_short = _short_idea_id(session, child_id)
    blocks, _, _ = _out(run_tool, capsys, session, anchor="none")
    assert blocks["Parent"]["obsoleted_by"] == [child_short]
    assert blocks["Child"]["obsoleted_by"] == []   # child has no obsoleting kids


def test_not_obsoleted_when_child_not_cheaper(session, run_tool, capsys):
    # Child costs the SAME as parent -> not a confident improvement -> no line.
    self_child = _obsoletion_setup(session, child_cost=200)
    blocks, _, _ = _out(run_tool, capsys, session, anchor="none")
    assert blocks["Parent"]["obsoleted_by"] == []
    del self_child  # (unused; setup returns the child id)


# ---- anchor warnings ------------------------------------------------------

def test_anchor_used_low_cost_warning(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _, ct = _idea(cat, ws, C0, "Fast", "a", [50, 50, 50])  # ratio 0.25 < 0.5
        set_id = add_synthetic_benchmark_set(
            cat, {ct: [[50, 50, 50]], C0: [[200, 200, 200]]})
        ws.add_private_benchmark_set(set_id, cat)
        ws.set_current_anchor(C0)
    _build(session, build)
    _, _, warnings = _out(run_tool, capsys, session, anchor="always")
    assert warnings == ["Warning: some ranked schedules were much faster than the anchor.",
                        "This amplifies the effects of system noise; consider a new anchor."]


def test_anchor_used_no_warning_when_costs_normal(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _, ct = _idea(cat, ws, C0, "Similar", "a", [180, 180, 180])  # ratio 0.9
        set_id = add_synthetic_benchmark_set(
            cat, {ct: [[180, 180, 180]], C0: [[200, 200, 200]]})
        ws.add_private_benchmark_set(set_id, cat)
        ws.set_current_anchor(C0)
    _build(session, build)
    blocks, _, warnings = _out(run_tool, capsys, session, anchor="always")
    assert warnings == []                       # anchored, no sub-0.5 cost
    assert blocks["Similar"]["cost"] == 0.9     # ratio, anchored


def test_lock_is_taken(session, run_tool, capsys):
    run_tool(tools.cmd_list_private_ideas, _ns(session, anchor="none"))
    assert ("session", "exclusive") in locks._trace_sink
