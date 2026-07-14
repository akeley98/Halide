"""Short-ID behavior under partial hash collisions.

Real sha256 prefix collisions are infeasible to brute-force in a test, but the
resolver/formatter derive the hash from the stored full ID (never recompute it
from source).  So we fabricate structurally-valid on-disk schedule nodes whose
hashes share a >=6-char prefix but differ later, and drive the collision paths
directly.
"""

import os

import pytest

from dendritic_hl_lib import ids
from dendritic_hl_lib.catalog import Catalog
from dendritic_hl_lib.errors import DhHlError


def _skeleton(tmp_path):
    cat_dir = str(tmp_path / "gen.cpp.dh_hl")
    os.makedirs(os.path.join(cat_dir, "sch"))
    os.makedirs(os.path.join(cat_dir, "idea"))
    (tmp_path / "gen.cpp").write_text("//ws")
    return cat_dir


def _make_root(cat_dir, ts, h):
    """Write a valid root schedule node on disk with a chosen hash *h*."""
    sid = ids.make_schedule_id(ts, h)
    assert ids.is_schedule_id(sid)
    d = os.path.join(cat_dir, "sch", sid)
    os.makedirs(d)
    with open(os.path.join(d, "generator.cpp"), "w") as f:
        f.write("// source for " + h[:8])
    return sid


def test_shared_prefix_ambiguous_at_6_disambiguated_at_7(tmp_path):
    cat_dir = _skeleton(tmp_path)
    # Share the first 6 hex chars ("aaaaaa"); differ at index 6.
    h1 = "aaaaaa" + "b" + "0" * 57
    h2 = "aaaaaa" + "c" + "0" * 57
    assert h1[:6] == h2[:6] and h1 != h2
    s1 = _make_root(cat_dir, ids.now_timestamp(), h1)
    s2 = _make_root(cat_dir, ids.now_timestamp(), h2)

    cat = Catalog(cat_dir, str(tmp_path / "gen.cpp"))

    # The 6-char prefix is ambiguous in both short-ID grammars.
    with pytest.raises(DhHlError, match="ambiguous|matches"):
        cat.resolve_schedule("root.aaaaaa")
    with pytest.raises(DhHlError, match="ambiguous|matches"):
        cat.resolve_schedule("aaaaaa")  # bare hash-prefix form

    # The formatter must extend past 6 chars to make it unambiguous, but keep
    # the shared 6-char prefix.
    short1 = cat.format_schedule_id(cat.schedules[s1])
    assert short1.startswith("root.aaaaaa")
    assert len(short1) > len("root.aaaaaa"), "should extend past the collision"
    assert cat.resolve_schedule(short1).full_id == s1

    short2 = cat.format_schedule_id(cat.schedules[s2])
    assert cat.resolve_schedule(short2).full_id == s2
    assert short1 != short2

    # And an explicit 7-char prefix resolves uniquely.
    assert cat.resolve_schedule("root.aaaaaab").full_id == s1
    assert cat.resolve_schedule("root.aaaaaac").full_id == s2


def test_ambiguity_error_lists_both_oldest_to_newest(tmp_path):
    cat_dir = _skeleton(tmp_path)
    older = _make_root(cat_dir, "2026-01-01T000000_000000Z", "aaaaaa" + "1" * 58)
    newer = _make_root(cat_dir, "2026-12-31T000000_000000Z", "aaaaaa" + "2" * 58)
    cat = Catalog(cat_dir, str(tmp_path / "gen.cpp"))
    with pytest.raises(DhHlError) as e:
        cat.resolve_schedule("root.aaaaaa")
    msg = str(e.value)
    assert older in msg and newer in msg
    assert msg.index(older) < msg.index(newer)  # oldest first


def test_identical_hash_falls_back_to_full_id(tmp_path):
    """Two roots with the *same* hash (different timestamps) can't be told
    apart by any hash prefix, so the formatter falls back to the full ID."""
    cat_dir = _skeleton(tmp_path)
    h = "d" * 64
    s1 = _make_root(cat_dir, "2026-01-01T000000_000000Z", h)
    s2 = _make_root(cat_dir, "2026-06-01T000000_000000Z", h)
    cat = Catalog(cat_dir, str(tmp_path / "gen.cpp"))
    short1 = cat.format_schedule_id(cat.schedules[s1])
    assert short1 == s1                     # gave up on a short form
    assert ids.is_schedule_id(short1)
    assert "." not in short1                # a full ID, not a short one
    assert cat.resolve_schedule(short1).full_id == s1
