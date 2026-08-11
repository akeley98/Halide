"""Cost model core (`cost.py`) + the private-benchmark-set cache that feeds it.

Deterministic and Halide-free: `add_synthetic_benchmark_set` fabricates benchmark
sub-objects with hand-chosen `wall_time_min` values, so every ranking/comparison
verdict below is computed against known inputs (idea.md testing notes for the
cost tools; the bootstrap CI is seeded, so verdicts are reproducible)."""

import math

from dendritic_hl_lib import cost, safety
from dendritic_hl_lib.enums import CostVerdict
from dendritic_hl_lib.catalog import EXPECTED_PROFILER_VERSION
from dendritic_hl_lib.context import SessionWorkspace, _benchmark_set_cache
from conftest import add_synthetic_benchmark_set, open_catalog


def _catalog(tmp_path, source="src"):
    """A catalog with a root R and three sibling child schedules A/B/C under one
    idea, all built from distinct source so they get distinct hashes/IDs."""
    cat_dir = str(tmp_path / "proj.dh_hl")
    cat = open_catalog(cat_dir)
    cat.ensure_created()
    R = cat.create_schedule("root", parent_idea=None)
    I = cat.create_idea(R, "idea", "prop\n")
    A = cat.create_schedule("A source", parent_idea=I)
    B = cat.create_schedule("B source", parent_idea=I)
    C = cat.create_schedule("C source", parent_idea=I)
    return cat, {"R": R.full_id, "A": A.full_id, "B": B.full_id, "C": C.full_id}


# ---- cache population -----------------------------------------------------

def test_benchmark_set_cache_shape(tmp_path):
    cat, t = _catalog(tmp_path)
    # A: 1 params index, 3 batches; B: same.
    set_id = add_synthetic_benchmark_set(cat, {
        t["A"]: [[100, 102, 101]],
        t["B"]: [[120, 119, 121]],
    }, hostname="h", cpu_count=8)
    cat.flush()
    safety.commit()

    cache = _benchmark_set_cache(cat, set_id)
    assert cache["hostname"] == "h"
    assert cache["cpu_count"] == 8
    assert cache["profiler_version"] == EXPECTED_PROFILER_VERSION
    a_cell = cache["schedules"][t["A"]][0]      # params index 0
    assert a_cell["wall_time_min"] == [100, 102, 101]
    assert len(a_cell["id"]) == 3
    assert all(bid.startswith(t["A"]) for bid in a_cell["id"])


def test_add_private_benchmark_set_roundtrip(tmp_path):
    cat, t = _catalog(tmp_path)
    set_id = add_synthetic_benchmark_set(
        cat, {t["A"]: [[100, 101, 102]]})
    cat.flush()
    safety.commit()

    ws = SessionWorkspace(cat.catalog_dir, "0_x", catalog=cat)
    ws.add_private_benchmark_set(set_id, cat)
    safety.commit()
    sets = ws.read_private_benchmark_sets()
    assert set(sets) == {set_id}
    assert sets[set_id]["schedules"][t["A"]][0]["wall_time_min"] == [100, 101, 102]

    ws.remove_private_benchmark_set(set_id)
    safety.commit()
    assert ws.read_private_benchmark_sets() == {}


# ---- ranking --------------------------------------------------------------

def test_ranking_without_anchor(tmp_path):
    cat, t = _catalog(tmp_path)
    set_id = add_synthetic_benchmark_set(cat, {
        t["A"]: [[100, 102, 101]],
        t["B"]: [[130, 130, 130]],
    })
    cat.flush(); safety.commit()
    data = cost.CostData.from_private_sets(_benchmark_set_dict(cat, set_id))

    a = data.ranking_cost(t["A"], None)
    assert a["batch_count"] == 3 and a["representative"] == 0
    assert a["cost"] == 101 and a["anchor"] is None          # median raw cost
    assert a["raw_costs"] == {0: 101}
    b = data.ranking_cost(t["B"], None)
    assert b["cost"] == 130


def test_ranking_with_anchor_is_ratio(tmp_path):
    cat, t = _catalog(tmp_path)
    set_id = add_synthetic_benchmark_set(cat, {
        t["A"]: [[100, 100, 100]],
        t["B"]: [[200, 200, 200]],   # anchor
    })
    cat.flush(); safety.commit()
    data = cost.CostData.from_private_sets(_benchmark_set_dict(cat, set_id))

    a = data.ranking_cost(t["A"], t["B"])
    assert a["anchor"] == t["B"]
    assert a["cost"] == 0.5                                   # 100 / 200
    assert a["raw_costs"] == {0: 100}                         # raw, not ratio


def test_representative_picks_lowest_median_param(tmp_path):
    cat, t = _catalog(tmp_path)
    # A has two params objects: index 1 is faster -> it is the representative.
    set_id = add_synthetic_benchmark_set(cat, {
        t["A"]: [[100, 100, 100], [70, 71, 69]],
    })
    cat.flush(); safety.commit()
    data = cost.CostData.from_private_sets(_benchmark_set_dict(cat, set_id))

    a = data.ranking_cost(t["A"], None)
    assert a["representative"] == 1
    assert a["cost"] == 70                                    # median of [70,71,69]
    assert a["raw_costs"] == {0: 100, 1: 70}


# ---- 2-way comparison -----------------------------------------------------

def test_compare_improvement_and_regression(tmp_path):
    cat, t = _catalog(tmp_path)
    set_id = add_synthetic_benchmark_set(cat, {
        t["A"]: [[100, 101, 99, 100, 102]],
        t["B"]: [[130, 129, 131, 130, 128]],
    })
    cat.flush(); safety.commit()
    data = cost.CostData.from_private_sets(_benchmark_set_dict(cat, set_id))

    ab = data.compare(t["A"], t["B"])
    assert ab["result"] is CostVerdict.IMPROVEMENT                     # A cheaper than B
    assert ab["batch_count"] == 5
    assert ab["lhs_raw_cost"] == 100 and ab["rhs_raw_cost"] == 130
    ba = data.compare(t["B"], t["A"])
    assert ba["result"] is CostVerdict.REGRESSION                      # B dearer than A
    assert data.is_improvement(t["A"], t["B"]) is True
    assert data.is_improvement(t["B"], t["A"]) is False


def test_compare_overlapping_is_unknown(tmp_path):
    cat, t = _catalog(tmp_path)
    # Fully interleaved/overlapping distributions -> CI straddles 0 -> unknown.
    set_id = add_synthetic_benchmark_set(cat, {
        t["A"]: [[100, 110, 90, 105, 95]],
        t["B"]: [[105, 95, 108, 92, 100]],
    })
    cat.flush(); safety.commit()
    data = cost.CostData.from_private_sets(_benchmark_set_dict(cat, set_id))
    assert data.compare(t["A"], t["B"])["result"] is CostVerdict.UNKNOWN


def test_compare_insufficient_data_is_unknown(tmp_path):
    cat, t = _catalog(tmp_path)
    set_id = add_synthetic_benchmark_set(cat, {
        t["A"]: [[100]], t["B"]: [[130]],   # single batch -> CI undefined
    })
    cat.flush(); safety.commit()
    data = cost.CostData.from_private_sets(_benchmark_set_dict(cat, set_id))
    r = data.compare(t["A"], t["B"])
    assert r["result"] is CostVerdict.UNKNOWN and r["batch_count"] == 1


def test_compare_no_shared_batches(tmp_path):
    """A and C never appear in the same set -> no paired batches -> unknown."""
    cat, t = _catalog(tmp_path)
    s1 = add_synthetic_benchmark_set(cat, {t["A"]: [[100, 101, 99]]})
    s2 = add_synthetic_benchmark_set(cat, {t["C"]: [[130, 131, 129]]})
    cat.flush(); safety.commit()
    private = {}
    private.update(_benchmark_set_dict(cat, s1))
    private.update(_benchmark_set_dict(cat, s2))
    data = cost.CostData.from_private_sets(private)
    r = data.compare(t["A"], t["C"])
    assert r["result"] is CostVerdict.UNKNOWN and r["batch_count"] == 0
    # But each still ranks on its own batches.
    assert data.ranking_cost(t["A"], None)["cost"] == 100
    assert data.ranking_cost(t["C"], None)["cost"] == 130


# ---- profiler-version gate ------------------------------------------------

def test_wrong_profiler_version_skipped(tmp_path):
    cat, t = _catalog(tmp_path)
    set_id = add_synthetic_benchmark_set(cat, {
        t["A"]: [[100, 101, 99]], t["B"]: [[130, 131, 129]],
    }, profiler_version=EXPECTED_PROFILER_VERSION + 1)
    cat.flush(); safety.commit()
    data = cost.CostData.from_private_sets(_benchmark_set_dict(cat, set_id))
    # The whole set is dropped -> no samples at all.
    assert data.ranking_cost(t["A"], None) == {
        "batch_count": 0, "cost": None, "anchor": None,
        "representative": None, "raw_costs": {}}


def test_version_mismatch_warns_with_set_id(tmp_path, capsys):
    """A discarded set is announced on stderr (naming the set + versions), so an
    all-null cost after a profiler bump isn't a silent mystery."""
    cat, t = _catalog(tmp_path)
    set_id = add_synthetic_benchmark_set(
        cat, {t["A"]: [[100, 101, 99]]},
        profiler_version=EXPECTED_PROFILER_VERSION + 1)
    cat.flush(); safety.commit()

    cost.CostData.from_private_sets(_benchmark_set_dict(cat, set_id))
    err = capsys.readouterr().err
    assert set_id in err
    assert "profiler_version" in err and "null" in err
    assert str(EXPECTED_PROFILER_VERSION) in err


# ---- bootstrap primitive determinism --------------------------------------

def test_paired_diff_ci_deterministic():
    diffs = [-30, -28, -31, -29, -32]
    first = cost.paired_diff_ci(diffs)
    second = cost.paired_diff_ci(diffs)
    assert first == second                                   # fixed seed
    med, lo, hi = first
    assert med == -30 and lo < 0 and hi < 0
    assert cost.compare_verdict(lo, hi) is CostVerdict.IMPROVEMENT


def test_paired_diff_ci_too_few_samples():
    med, lo, hi = cost.paired_diff_ci([5])
    assert math.isnan(med) and math.isnan(lo) and math.isnan(hi)
    assert cost.compare_verdict(lo, hi) is CostVerdict.UNKNOWN


# ---- helpers --------------------------------------------------------------

def _benchmark_set_dict(cat, set_id):
    """The {set_id: cache} mapping the cost core consumes, for one set."""
    return {set_id: _benchmark_set_cache(cat, set_id)}
