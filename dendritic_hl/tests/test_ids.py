"""ID/timestamp/hash primitives, including Hypothesis round-trip properties."""

import re

from hypothesis import given, strategies as st

from dendritic_hl_lib import ids


def test_timestamp_shape_and_length():
    ts = ids.now_timestamp()
    assert len(ts) == ids.TIMESTAMP_LEN == 25
    assert ids.is_timestamp(ts)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d{6}_\d{6}Z", ts)


def test_schedule_id_is_exactly_90_chars():
    ts = "2026-07-14T201921_047391Z"
    h = "a" * 64
    sid = ids.make_schedule_id(ts, h)
    assert len(sid) == 90
    assert ids.is_schedule_id(sid)
    assert ids.schedule_timestamp(sid) == ts
    assert ids.schedule_hash(sid) == h


def test_idea_id_split_is_unambiguous():
    ts = "2026-07-14T201921_047391Z"
    sid = ids.make_schedule_id(ts, "b" * 64)
    # A proposal name that itself contains underscores must still round-trip,
    # because the schedule ID tail is fixed width.
    iid = ids.make_idea_id("my_cool_idea", sid)
    assert ids.is_idea_id(iid)
    assert ids.idea_proposal_name(iid) == "my_cool_idea"
    assert ids.idea_parent_id(iid) == sid


def test_bad_ids_rejected():
    assert not ids.is_schedule_id("too short")
    assert not ids.is_schedule_id("x" * 90)          # not hex tail / bad ts
    assert not ids.is_idea_id("noparent")
    assert not ids.is_timestamp("2026-7-14T2019_0Z")  # wrong widths


_names = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_",
    min_size=1, max_size=72)
_hashes = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
_timestamps = st.builds(
    lambda *_: ids.now_timestamp())  # shape is fixed; value irrelevant


@given(h=_hashes)
def test_schedule_id_roundtrip(h):
    ts = ids.now_timestamp()
    sid = ids.make_schedule_id(ts, h)
    assert ids.is_schedule_id(sid)
    assert ids.schedule_hash(sid) == h
    assert ids.schedule_timestamp(sid) == ts


@given(name=_names, h=_hashes)
def test_idea_id_roundtrip(name, h):
    sid = ids.make_schedule_id(ids.now_timestamp(), h)
    iid = ids.make_idea_id(name, sid)
    assert ids.is_idea_id(iid)
    assert ids.idea_proposal_name(iid) == name
    assert ids.idea_parent_id(iid) == sid


@given(data=st.binary(max_size=200))
def test_hash_is_64_lower_hex(data):
    h = ids.sha256_hex(data)
    assert re.fullmatch(r"[0-9a-f]{64}", h)
