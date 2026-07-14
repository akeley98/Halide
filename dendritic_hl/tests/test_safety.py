"""The safety module: rollback ordering, rmdir guard, deferred overwrites."""

import os

import pytest


def test_new_file_created_and_recorded(reset_safety, tmp_path):
    s = reset_safety
    p = str(tmp_path / "a.txt")
    s.new_file(p, "hi")
    assert os.path.exists(p)
    assert ("file", p) in s._new_entries


def test_rollback_removes_in_reverse_and_empties_dirs(reset_safety, tmp_path):
    s = reset_safety
    d = str(tmp_path / "sch" / "node")
    s.makedirs_tracked(d)
    f1 = os.path.join(d, "generator.cpp")
    f2 = os.path.join(d, "parent.txt")
    s.new_file(f1, "x")
    s.new_file(f2, "y")
    assert os.path.isdir(d) and os.path.exists(f1) and os.path.exists(f2)
    s._rollback()
    # Files gone, and the dirs we created (sch/, sch/node/) are gone too.
    assert not os.path.exists(f1) and not os.path.exists(f2)
    assert not os.path.isdir(d)
    assert not os.path.isdir(str(tmp_path / "sch"))
    assert s._new_entries == []


def test_rollback_never_deletes_preexisting_dir(reset_safety, tmp_path):
    s = reset_safety
    existing = tmp_path / "already_here"
    existing.mkdir()
    # makedirs_tracked into the existing dir should only record the NEW leaf.
    d = str(existing / "leaf")
    s.makedirs_tracked(d)
    f = os.path.join(d, "f.txt")
    s.new_file(f, "z")
    s._rollback()
    assert not os.path.isdir(d)          # our leaf is gone
    assert existing.is_dir()             # pre-existing dir survives


def test_rollback_swallows_nonempty_dir(reset_safety, tmp_path):
    """If a stray file we didn't record lands in a created dir, os.rmdir fails;
    rollback must swallow it and not loop forever."""
    s = reset_safety
    d = str(tmp_path / "d")
    s.makedirs_tracked(d)
    # An unrecorded file makes the dir non-empty.
    with open(os.path.join(d, "stray"), "w") as f:
        f.write("!")
    s._rollback()  # must return, not raise / hang
    assert os.path.isdir(d)  # left intact because it wasn't empty


def test_write_allowed_new_then_overwrite(reset_safety, tmp_path):
    s = reset_safety
    p = str(tmp_path / "result.txt")
    # First write: file absent -> created via new_file (rollback-eligible).
    s.write_allowed(p, "success\n")
    assert ("file", p) in s._new_entries
    assert os.path.exists(p)
    s.commit()  # clears entries; keeps file
    assert os.path.exists(p)
    assert s._new_entries == []
    # Second write: file present -> deferred overwrite, NOT recorded.
    s.write_allowed(p, "halide error\n")
    assert s._new_entries == []
    assert open(p).read() == "success\n"      # not yet applied
    s.commit()
    assert open(p).read() == "halide error\n"  # applied on commit


def test_commit_disarms_rollback(reset_safety, tmp_path):
    s = reset_safety
    p = str(tmp_path / "keep.txt")
    s.new_file(p, "data")
    s.commit()
    s._rollback()               # no-op now
    assert os.path.exists(p)    # survives
