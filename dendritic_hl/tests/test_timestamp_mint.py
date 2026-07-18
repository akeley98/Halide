"""Timestamp minting (impl.md "Tool Safety: Timestamp Conflicts").

Catalog.mint_timestamped_name re-mints a fresh timestamp whenever the derived
catalog path already exists on disk.  The os.path.exists re-mint branch is what
these tests pin, deterministically, by controlling the timestamp source and
pre-seeding a candidate path.

FUTURE: the genuine failure this mitigates -- two processes minting the *same*
microsecond timestamp for the same content under the catalog lock -- is a real
concurrency race that is not practically reproducible in a unit test, so it is
left uncovered on purpose (see idea.md / impl.md).  What we cover here is the
mechanism (skip an already-taken name), not the microsecond timing.
"""

import os

from dendritic_hl_lib.catalog import Catalog


def _catalog(tmp_path):
    cat = Catalog(str(tmp_path / "proj.dh_hl"))
    cat.ensure_created()
    return cat


def test_mint_returns_first_when_path_free(tmp_path, monkeypatch):
    cat = _catalog(tmp_path)
    seq = iter(["T1", "T2"])
    monkeypatch.setattr(cat, "fresh_timestamp", lambda: next(seq))
    ts = cat.mint_timestamped_name(
        lambda t: os.path.join(cat.catalog_dir, "cand_" + t))
    assert ts == "T1"  # first candidate free => no re-mint


def test_mint_remints_past_existing_path(tmp_path, monkeypatch):
    cat = _catalog(tmp_path)
    seq = iter(["T1", "T2", "T3"])
    monkeypatch.setattr(cat, "fresh_timestamp", lambda: next(seq))

    seen = []

    def build_path(t):
        seen.append(t)
        return os.path.join(cat.catalog_dir, "cand_" + t)

    # Pre-seed the first candidate so the mint must skip it.
    os.mkdir(os.path.join(cat.catalog_dir, "cand_T1"))

    ts = cat.mint_timestamped_name(build_path)
    assert ts == "T2"
    assert seen[:2] == ["T1", "T2"]  # T1 stat'd + rejected, T2 accepted


def test_create_schedule_remints_on_dir_collision(tmp_path, monkeypatch):
    """End-to-end: if the sch/{id} dir a schedule would take already exists,
    create_schedule mints a later timestamp instead of colliding."""
    cat = _catalog(tmp_path)
    from dendritic_hl_lib import ids

    seq = iter(["2026-01-01T000000_000001Z", "2026-01-01T000000_000002Z"])
    monkeypatch.setattr(cat, "fresh_timestamp", lambda: next(seq))

    source = "some source"
    h = ids.sha256_hex(source)
    taken = ids.make_schedule_id("2026-01-01T000000_000001Z", h)
    os.mkdir(os.path.join(cat.sch_dir, taken))  # squat the first candidate

    node = cat.create_schedule(source, parent_idea=None)
    assert node.timestamp == "2026-01-01T000000_000002Z"
