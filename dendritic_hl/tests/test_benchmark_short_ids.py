"""Benchmark short IDs: the session-scoped `private.{schedule}.{i}.{n}` form
(idea.md "Benchmark short ID").

These exercise the record / format / resolve path WITHOUT Halide, using synthetic
benchmark sub-objects.  The end-to-end `build` printing + resolution is covered by
the halide tests (test_build_cli_halide.py).

The storage is SHARDED -- one JSON list per (schedule, params index) at
`benchmark_short_id/{schedule full ID}/{params index}.json` -- so any one
operation only touches a handful of schedules, never the whole (potentially
huge) benchmark database.  There is deliberately no reverse full-ID -> short-ID
search; `build` formats directly from the `n` that `record` returns."""

import json
import os

import pytest

from dendritic_hl_lib import locks, safety
from dendritic_hl_lib.context import Context, SessionWorkspace
from dendritic_hl_lib.errors import DhHlError
from conftest import make_profiler_obj, open_catalog


def _bench(node, hostname="host", pidx=0):
    """Attach a synthetic benchmark sub-object to *node* and return it."""
    return node.add_benchmark(hostname, {
        "hostname": hostname, "cpu_count": 8, "parameters": {},
        "parameters_index": pidx, "problem": None,
        "profiler": make_profiler_obj(100, profiler_version=1),
        "warnings": [], "stdout": ""})


def _child_with_benchmarks(cat, session_id, per_pidx):
    """Create a child schedule and, for each params index, `per_pidx[i]` synthetic
    benchmarks recorded in the session's benchmark-short-ID shards.  Returns
    (node, ws, [[bench, ...] per pidx])."""
    idea = cat.get_idea(cat.get_session(session_id).seed_idea_id)
    node = cat.create_schedule("child source\n", parent_idea=idea)
    ws = SessionWorkspace(cat.catalog_dir, session_id, catalog=cat)
    benches = []
    for pidx, count in enumerate(per_pidx):
        row = []
        for _ in range(count):
            b = _bench(node, pidx=pidx)
            n = ws.record_benchmark(node.full_id, pidx, b.full_id, cat)
            assert n == len(row)           # 0-based, in creation order
            row.append(b)
        benches.append(row)
    return node, ws, benches


# ---- format (direct, no reverse lookup) -------------------------------------

def test_format_produces_private_short_form(session):
    cat = open_catalog(session.catalog_dir)
    try:
        node, ws, _benches = _child_with_benchmarks(cat, session.session_id, [2, 1])
        sched_short = cat.format_schedule_id(node)
        # Format is built from the (node, params index, n) the caller already has.
        assert ws.format_benchmark_short_id(node, 0, 0, cat) == \
            "private.{}.0.0".format(sched_short)
        assert ws.format_benchmark_short_id(node, 0, 1, cat) == \
            "private.{}.0.1".format(sched_short)
        assert ws.format_benchmark_short_id(node, 1, 0, cat) == \
            "private.{}.1.0".format(sched_short)
    finally:
        locks._reset_for_tests()


# ---- resolve ----------------------------------------------------------------

def test_resolve_round_trips_format(session):
    cat = open_catalog(session.catalog_dir)
    try:
        node, ws, benches = _child_with_benchmarks(cat, session.session_id, [2])
        for n, b in enumerate(benches[0]):
            spec = ws.format_benchmark_short_id(node, 0, n, cat)
            assert spec == "private.{}.0.{}".format(cat.format_schedule_id(node), n)
            assert ws.resolve_benchmark_short_id(spec, cat).full_id == b.full_id
    finally:
        locks._reset_for_tests()


def test_resolve_accepts_full_schedule_id_in_short_form(session):
    """The schedule part of a private short ID may be a full ID, not just short."""
    cat = open_catalog(session.catalog_dir)
    try:
        node, ws, benches = _child_with_benchmarks(cat, session.session_id, [1])
        spec = "private.{}.0.0".format(node.full_id)
        assert ws.resolve_benchmark_short_id(spec, cat).full_id == \
            benches[0][0].full_id
    finally:
        locks._reset_for_tests()


@pytest.mark.parametrize("spec", [
    "private.NOPE.0.0",                 # unresolvable schedule
    "private.only.two",                 # too few dot-parts
    "private.sched.x.0",                # non-integer params index
    "private.sched.0.y",                # non-integer n
])
def test_resolve_bad_forms_raise(session, spec):
    cat = open_catalog(session.catalog_dir)
    try:
        _child_with_benchmarks(cat, session.session_id, [1])
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        with pytest.raises(DhHlError):
            ws.resolve_benchmark_short_id(spec, cat)
    finally:
        locks._reset_for_tests()


def test_resolve_out_of_range_n_raises(session):
    cat = open_catalog(session.catalog_dir)
    try:
        node, ws, _ = _child_with_benchmarks(cat, session.session_id, [1])
        spec = "private.{}.0.5".format(cat.format_schedule_id(node))
        with pytest.raises(DhHlError, match="no benchmark matches short ID"):
            ws.resolve_benchmark_short_id(spec, cat)
    finally:
        locks._reset_for_tests()


# ---- sharded storage + persistence ------------------------------------------

def test_storage_is_sharded_per_schedule_and_index(session):
    """Each (schedule, params index) is its own file under a per-schedule dir; no
    single global file is written."""
    cat = open_catalog(session.catalog_dir)
    try:
        node, ws, _ = _child_with_benchmarks(cat, session.session_id, [1, 1])
        cat.flush(); safety.commit()
        root = ws.benchmark_short_ids_dir
        assert not os.path.exists(root + ".json")        # no single global file
        assert os.path.isfile(os.path.join(root, node.full_id, "0.json"))
        assert os.path.isfile(os.path.join(root, node.full_id, "1.json"))
        with open(os.path.join(root, node.full_id, "0.json")) as f:
            assert isinstance(json.load(f), list)        # a bare list per shard
    finally:
        locks._reset_for_tests()


def test_recorded_shards_persist_every_item(session):
    cat = open_catalog(session.catalog_dir)
    try:
        node, ws, benches = _child_with_benchmarks(cat, session.session_id, [3])
        node_id = node.full_id
        full_ids = [b.full_id for b in benches[0]]
        cat.flush(); safety.commit()
    finally:
        locks._reset_for_tests()
    # Re-open a fresh workspace so it lazy-loads the shard from disk.
    cat = open_catalog(session.catalog_dir)
    try:
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        for n, fid in enumerate(full_ids):
            assert ws.benchmark_short_ids.lookup(node_id, 0, n) == fid
        assert ws.benchmark_short_ids.lookup(node_id, 0, 3) is None
    finally:
        locks._reset_for_tests()


def test_record_across_builds_continues_n(session):
    """A second session process appends AFTER the on-disk shard, so `n` keeps
    counting from where the prior run left off (idea.md creation order)."""
    cat = open_catalog(session.catalog_dir)
    try:
        idea = cat.get_idea(cat.get_session(session.session_id).seed_idea_id)
        node = cat.create_schedule("child source\n", parent_idea=idea)
        node_id = node.full_id
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        assert ws.record_benchmark(node_id, 0, _bench(node).full_id, cat) == 0
        cat.flush(); safety.commit()
    finally:
        locks._reset_for_tests()
    cat = open_catalog(session.catalog_dir)
    try:
        node = cat.get_schedule(node_id)
        ws = SessionWorkspace(cat.catalog_dir, session.session_id, catalog=cat)
        # Fresh process: n continues at 1, not back to 0.
        assert ws.record_benchmark(node_id, 0, _bench(node).full_id, cat) == 1
    finally:
        locks._reset_for_tests()


def test_record_without_catalog_raises_before_mutating(session):
    """A catalog-less record refuses up front (no partial in-memory mutation that
    would then never be dirtied/flushed)."""
    cat = open_catalog(session.catalog_dir)
    try:
        node, _ws, _ = _child_with_benchmarks(cat, session.session_id, [1])
        detached = SessionWorkspace(cat.catalog_dir, session.session_id)  # no cat
        with pytest.raises(DhHlError, match="without a locked catalog"):
            detached.benchmark_short_ids.record(node.full_id, 0, "x")
        # Nothing was appended, so a real record still gets n == 0 for a fresh key.
        assert detached.benchmark_short_ids.lookup(node.full_id, 0, 0) is None
    finally:
        locks._reset_for_tests()


# ---- catalog layer: the old short form is GONE ------------------------------

def test_catalog_resolve_benchmark_full_id_only(session):
    """`Catalog.resolve_benchmark` handles full IDs but rejects the old
    `{schedule short}.{host}_{ts}` short form and the `private.` form (those need
    a session)."""
    cat = open_catalog(session.catalog_dir)
    try:
        node, ws, benches = _child_with_benchmarks(cat, session.session_id, [1])
        b = benches[0][0]
        assert cat.resolve_benchmark(b.full_id).full_id == b.full_id
        sched_short = cat.format_schedule_id(node)
        # Former short form: schedule short id + '.' + local id -> now invalid.
        with pytest.raises(DhHlError, match="not a valid benchmark full ID"):
            cat.resolve_benchmark("{}.{}".format(sched_short, b.local_id))
        with pytest.raises(DhHlError, match="not a valid benchmark full ID"):
            cat.resolve_benchmark("private.{}.0.0".format(sched_short))
    finally:
        locks._reset_for_tests()


def test_context_resolve_benchmark_arg_dispatches(session):
    """Context.resolve_benchmark_arg: full ID via the catalog, private. form via
    the session workspace."""
    cat = open_catalog(session.catalog_dir)
    try:
        node, _ws, benches = _child_with_benchmarks(cat, session.session_id, [1])
        b = benches[0][0]
        cat.flush(); safety.commit()   # ctx.workspace is a fresh on-disk reader
        ctx = Context(cat, session.session_id)
        assert ctx.resolve_benchmark_arg(b.full_id).full_id == b.full_id
        spec = "private.{}.0.0".format(cat.format_schedule_id(node))
        assert ctx.resolve_benchmark_arg(spec).full_id == b.full_id
    finally:
        locks._reset_for_tests()
