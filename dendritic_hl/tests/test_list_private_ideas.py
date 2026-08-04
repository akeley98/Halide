"""The cost-ranked `list_private_ideas` frontier (idea.md "List Session Private
Ideas Tool").  Deterministic: synthetic benchmark sets give each idea's schedule
a known cost, so ranking order, the batch_count/cost lines, the pool grouping and
filters, obsoleted-by detection, and the drift warnings are all checked against
hand-chosen numbers."""

from dendritic_hl_lib import locks, safety, tools
from dendritic_hl_lib.context import SessionWorkspace
from conftest import add_synthetic_benchmark_set, open_catalog


def _out(run_tool, capsys, session, **ns_over):
    capsys.readouterr()
    run_tool(tools.cmd_list_private_ideas, _ns(session, **ns_over))
    return capsys.readouterr().out


def _ns(session, **kw):
    kw.setdefault("anchor", "auto")
    kw.setdefault("confidence", None)
    kw.setdefault("max", None)
    kw.setdefault("pool", None)
    kw.setdefault("pools", None)
    kw.setdefault("done", False)
    kw.setdefault("todo", False)
    return session.ns(**kw)


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
    """Create an idea `name` (pool tag *pool*) under a major schedule; if
    *cost_batches* is given, also a canonical schedule to be benchmarked at those
    per-batch costs.  Returns (idea_full_id, canonical_full_id | None)."""
    parent = cat.get_schedule(parent_sched_id)
    idea = cat.create_idea(parent, name, name + " proposal\n")
    canon = None
    if cost_batches is not None:
        dup = cat.create_schedule(name + " src\n", parent_idea=idea)
        dup.set_result("success")
        idea.set_canonical(dup.full_id)
        canon = dup.full_id
    ws.set_pool_tag(idea.full_id, pool)
    return idea.full_id, canon


# ---- ranking + grouping ---------------------------------------------------

def test_grouped_sorted_by_cost_with_drift_warning(session, run_tool, capsys):
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

    out = _out(run_tool, capsys, session, anchor="none")
    lines = out.splitlines()
    # Pool banners in sorted order: a, b, default (the seed idea is "default").
    banners = [l for l in lines if l.startswith("=== ")]
    assert banners == ["=== a ===", "=== b ===", "=== default ==="]
    # Within pool a, the cheaper idea ranks first.
    assert out.index("Acheap") < out.index("Aexpensive")
    # cost + batch_count lines are present with the chosen values.
    assert "  cost: 150" in out and "  cost: 200" in out
    assert "  batch_count: 3" in out
    # No anchor -> drift warning.
    assert "Warning: ranking is drift-exposed until you set an anchor." in out


def test_null_cost_bubbles_to_top(session, run_tool, capsys):
    """An idea whose schedule has no benchmarks sorts as cost 0 (top)."""
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _, cben = _idea(cat, ws, C0, "Benched", "p", [200, 201, 199])
        _idea(cat, ws, C0, "Unbenched", "p", [])   # canonical, but not benchmarked
        set_id = add_synthetic_benchmark_set(cat, {cben: [[200, 201, 199]]})
        ws.add_private_benchmark_set(set_id, cat)
    _build(session, build)
    out = _out(run_tool, capsys, session, anchor="none")
    assert out.index("Unbenched") < out.index("Benched")   # null(=0) first
    assert "  cost: null" in out


# ---- filters --------------------------------------------------------------

def test_todo_done_filters(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _idea(cat, ws, C0, "HasCanon", "p", [100, 101, 99])
        _idea(cat, ws, C0, "NoCanon", "p", None)   # no canonical schedule
    _build(session, build)

    done = _out(run_tool, capsys, session, done=True, pool=["p"])
    assert "HasCanon" in done and "NoCanon" not in done
    todo = _out(run_tool, capsys, session, todo=True, pool=["p"])
    assert "NoCanon" in todo and "HasCanon" not in todo


def test_max_truncates_per_pool(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        for i in range(4):
            _idea(cat, ws, C0, "Idea{}".format(i), "p", [100 + i, 100 + i, 100 + i])
    _build(session, build)
    out = _out(run_tool, capsys, session, pool=["p"], max=2)
    shown = sum("Idea{}".format(i) in out for i in range(4))
    assert shown == 2   # only the 2 cheapest of pool p


def test_pool_and_pools_filters(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _idea(cat, ws, C0, "InVec", "vec", [100, 100, 100])
        _idea(cat, ws, C0, "InTile", "tile", [100, 100, 100])
        _idea(cat, ws, C0, "InOther", "misc", [100, 100, 100])
    _build(session, build)

    only_vec = _out(run_tool, capsys, session, pool=["vec"])
    assert "InVec" in only_vec and "InTile" not in only_vec
    # --pools regex unions; matches vec + tile, not misc / default.
    rx = _out(run_tool, capsys, session, pools=["vec|tile"])
    assert "InVec" in rx and "InTile" in rx and "InOther" not in rx


# ---- obsoleted-by ---------------------------------------------------------

def test_obsoleted_by_child_idea(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        _, cp = _idea(cat, ws, C0, "Parent", "a", [200, 201, 199, 200, 202])
        # A child idea of Parent's canonical, with a cheaper canonical.
        child = cat.create_idea(cat.get_schedule(cp), "Child", "child\n")
        cc = cat.create_schedule("child src\n", parent_idea=child)
        cc.set_result("success")
        child.set_canonical(cc.full_id)
        ws.set_pool_tag(child.full_id, "a")
        set_id = add_synthetic_benchmark_set(
            cat, {cp: [[200, 201, 199, 200, 202]],
                  cc.full_id: [[100, 101, 99, 100, 98]]})
        ws.add_private_benchmark_set(set_id, cat)
        return child.full_id
    child_id = _build(session, build)

    out = _out(run_tool, capsys, session, anchor="none")
    # Parent is obsoleted by Child; the line names the child idea.
    obsoleted = [l for l in out.splitlines() if "obsoleted by:" in l]
    assert len(obsoleted) == 1
    cat = open_catalog(session.catalog_dir)
    try:
        child_short = cat.format_idea_id(cat.get_idea(child_id))
    finally:
        locks._reset_for_tests()
    assert child_short in obsoleted[0]


# ---- anchor warnings ------------------------------------------------------

def test_anchor_low_cost_warning(session, run_tool, capsys):
    def build(cat, ws):
        C0 = _seed_canonical(cat, session)
        # Anchor at 200; target much faster (ratio 0.25 < 0.5).
        _, ct = _idea(cat, ws, C0, "Fast", "a", [50, 50, 50])
        set_id = add_synthetic_benchmark_set(
            cat, {ct: [[50, 50, 50]], C0: [[200, 200, 200]]})
        ws.add_private_benchmark_set(set_id, cat)
        ws.set_current_anchor(C0)
    _build(session, build)

    out = _out(run_tool, capsys, session, anchor="always")
    assert "much faster than the anchor" in out
    assert "drift-exposed" not in out   # anchor WAS used


def test_lock_is_taken(session, run_tool, capsys):
    run_tool(tools.cmd_list_private_ideas, _ns(session, anchor="none"))
    assert ("session", "exclusive") in locks._trace_sink
