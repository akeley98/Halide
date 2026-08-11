"""End-to-end CLI coverage for the commentary `review`/`cancels` model, the
derived schedule/idea `review`, and idea side links (idea.md "Commentary
State", "Idea Node State").  Exercises json_schedule_info, json_idea_info, and
json_export together, per the IMPL TASKs in idea.md.
"""

import json

from dendritic_hl_lib import safety, tools
from dendritic_hl_lib.enums import Result
from conftest import ns, open_catalog


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _build(tmp_path):
    """R -> idea I(vec){canonical=C1, minor child C2}, idea I2(tile){minor child
    C3, no canonical}.  Returns (cat_dir, id-map)."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    I = cat.create_idea(R, "vec", "Vectorize.\n")
    C1 = cat.create_schedule("child one", parent_idea=I)
    C2 = cat.create_schedule("child two", parent_idea=I)
    I2 = cat.create_idea(R, "tile", "Tile.\n")
    C3 = cat.create_schedule("child three", parent_idea=I2)
    for c in (C1, C2, C3):
        c.set_result(Result.SUCCESS)
    I.set_canonical(C1.full_id)
    cat.flush()
    safety.commit()
    return cat_dir, {"R": R.full_id, "I": I.full_id, "I2": I2.full_id,
                     "C1": C1.full_id, "C2": C2.full_id, "C3": C3.full_id}


def _comment(run_tool, cat_dir, tmp_path, sched, text, review, cancels=None):
    cfile = _write(tmp_path, "c_{}.txt".format(abs(hash(text)) % 10000), text)
    run_tool(tools.cmd_comment, ns(catalog=cat_dir, commentary=cfile,
                                   schedule=sched, review=review,
                                   cancels=cancels))


def _sched_json(run_tool, capsys, cat_dir, sched):
    run_tool(tools.cmd_json_schedule_info, ns(catalog=cat_dir, schedule=sched))
    return json.loads(capsys.readouterr().out)


def _idea_json(run_tool, capsys, cat_dir, idea):
    run_tool(tools.cmd_json_idea_info, ns(catalog=cat_dir, idea=idea))
    return json.loads(capsys.readouterr().out)


def test_schedule_review_mixed_and_idea_inheritance(tmp_path, run_tool, capsys):
    cat_dir, t = _build(tmp_path)
    # C1 (canonical of I): one positive + one negative -> mixed.
    _comment(run_tool, cat_dir, tmp_path, t["C1"], "good", "positive")
    _comment(run_tool, cat_dir, tmp_path, t["C1"], "bad", "negative")
    capsys.readouterr()

    sj = _sched_json(run_tool, capsys, cat_dir, t["C1"])
    assert sj["review"] == "mixed"
    assert len(sj["commentary"]) == 2
    reviews = {c["review"] for c in sj["commentary"]}
    assert reviews == {"positive", "negative"}
    for c in sj["commentary"]:
        assert c["cancels"] == [] and c["cancelled_by"] == []

    # Idea I inherits its canonical schedule's (C1) review.
    ij = _idea_json(run_tool, capsys, cat_dir, t["I"])
    assert ij["review"] == "mixed"


def test_minor_schedule_commentary_ignored_for_idea(tmp_path, run_tool, capsys):
    cat_dir, t = _build(tmp_path)
    # C2 is a MINOR child of I; its commentary never feeds I's review (only the
    # canonical C1 does).  C1 has no commentary here -> neutral -> I neutral.
    _comment(run_tool, cat_dir, tmp_path, t["C2"], "minor note", "positive")
    capsys.readouterr()

    sj = _sched_json(run_tool, capsys, cat_dir, t["C2"])
    assert sj["review"] == "positive"           # the node's own review
    ij = _idea_json(run_tool, capsys, cat_dir, t["I"])
    assert ij["review"] == "neutral"            # canonical C1 has no commentary
    # I2 has no canonical schedule at all -> neutral.
    ij2 = _idea_json(run_tool, capsys, cat_dir, t["I2"])
    assert ij2["review"] == "neutral"
    assert ij2["canonical_schedule"] is None


def test_cancels_and_cancelled_by(tmp_path, run_tool, capsys):
    cat_dir, t = _build(tmp_path)
    # C3: a negative comment, then a positive one that cancels it.
    _comment(run_tool, cat_dir, tmp_path, t["C3"], "regression", "negative")
    capsys.readouterr()
    sj = _sched_json(run_tool, capsys, cat_dir, t["C3"])
    neg_id = sj["commentary"][0]["id"]
    assert sj["review"] == "negative"

    _comment(run_tool, cat_dir, tmp_path, t["C3"], "actually fine", "positive",
             cancels=[neg_id])
    capsys.readouterr()
    sj = _sched_json(run_tool, capsys, cat_dir, t["C3"])
    by_text = {c["text"]: c for c in sj["commentary"]}
    neg = by_text["regression"]
    pos = by_text["actually fine"]
    assert pos["cancels"] == [neg_id]
    assert neg["cancelled_by"] == [pos["id"]]
    # Non-cancelled set is just the positive -> derived review positive.
    assert sj["review"] == "positive"


def test_cancels_rejects_cross_node_target(tmp_path, run_tool, capsys):
    import pytest
    from dendritic_hl_lib.errors import DhHlError
    cat_dir, t = _build(tmp_path)
    _comment(run_tool, cat_dir, tmp_path, t["C1"], "on c1", "neutral")
    capsys.readouterr()
    c1_comment = _sched_json(run_tool, capsys, cat_dir, t["C1"])["commentary"][0]["id"]
    # Attempt to cancel C1's commentary from a comment on C3 -> rejected.
    with pytest.raises(DhHlError, match="same schedule node"):
        _comment(run_tool, cat_dir, tmp_path, t["C3"], "sneaky", "neutral",
                 cancels=[c1_comment])


def test_idea_side_links(tmp_path, run_tool, capsys):
    cat_dir, t = _build(tmp_path)
    run_tool(tools.cmd_add_idea_side_link,
             ns(catalog=cat_dir, idea_lhs=t["I"], type="borrows_from",
                idea_rhs=t["I2"]))
    # Exact duplicate is a silent no-op.
    run_tool(tools.cmd_add_idea_side_link,
             ns(catalog=cat_dir, idea_lhs=t["I"], type="borrows_from",
                idea_rhs=t["I2"]))
    capsys.readouterr()

    ij = _idea_json(run_tool, capsys, cat_dir, t["I"])
    assert ij["idea_side_links"] == [{"id": t["I2"], "type": "borrows_from"}]

    run_tool(tools.cmd_list_child_ideas, ns(catalog=cat_dir, schedule=t["R"]))
    listing = capsys.readouterr().out
    assert "borrowed from:" in listing


def test_json_export_includes_review_and_links(tmp_path, run_tool, capsys):
    cat_dir, t = _build(tmp_path)
    _comment(run_tool, cat_dir, tmp_path, t["C1"], "great", "positive")
    run_tool(tools.cmd_add_idea_side_link,
             ns(catalog=cat_dir, idea_lhs=t["I2"], type="superseded_by",
                idea_rhs=t["I"]))
    capsys.readouterr()

    run_tool(tools.cmd_json_export, ns(catalog=cat_dir))
    obj = json.loads(capsys.readouterr().out)
    assert set(obj) == {"ideas", "schedules", "sessions", "benchmark_sets",
                        "problems"}
    # Schedule review + commentary shape present in the export.
    c1 = obj["schedules"][t["C1"]]
    assert c1["review"] == "positive"
    assert c1["commentary"][0]["review"] == "positive"
    assert "cancelled_by" in c1["commentary"][0]
    # Idea review inherited + side link exported.
    assert obj["ideas"][t["I"]]["review"] == "positive"
    assert obj["ideas"][t["I2"]]["idea_side_links"] == [
        {"id": t["I"], "type": "superseded_by"}]
    # importance is fully gone.
    assert "importance" not in obj["ideas"][t["I"]]
