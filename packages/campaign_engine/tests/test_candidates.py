from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from campaign_engine.candidates import Arm, build_candidates, load_history
from campaign_engine.segments import SegmentIndex


def candidate_profile():
    return pd.DataFrame(
        {
            "ID_NUMBER": [50, 10, 40, 20, 30],
            "current_tariff": ["tariff_1", "tariff_1", None, "tariff_1", "tariff_1"],
            "arpu_segment": ["MID", "MID", "MID", "MID", None],
            "data_segment": ["HEAVY", None, "LITE", "LITE", "HEAVY"],
            "call_segment": ["HIGH", "LOW", "LOW", "MEDIUM", "HIGH"],
            "predicted_arpu": [5000, 1000, 4000, 2000, 3000],
        }
    )


def candidate_tariffs(count=4):
    return pd.DataFrame({"tariff_plan_code": [f"tariff_{i}" for i in range(1, count + 1)]})


def candidate_history(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "AVG_ARPU_PREV_3M",
            "AVG_ARPU_NEXT_3M",
            "tariff_plan_code_from",
            "tariff_plan_code_to",
        ],
    )


def test_index_preserves_missing_values_and_official_id_prefix():
    source = candidate_profile()
    index = SegmentIndex(source)
    assert index.profile["ID_NUMBER"].tolist() == [10, 20, 30, 40, 50]
    assert index.excluded_count == 2
    assert index.cells["tariff_1", "MID"].tolist() == [0, 1, 4]
    assert index.profile.loc[0, "data_segment"] is None
    assert source["ID_NUMBER"].tolist() == [50, 10, 40, 20, 30]
    assert index.arpu[index.cells["tariff_1", "MID"][:2]].sum() == 3000


def test_variants_include_nonempty_filters_and_keep_missing_data_in_base():
    index = SegmentIndex(candidate_profile())
    arm = Arm("tariff_1", "MID", "tariff_2")
    variants = index.variants(arm)
    assert variants is index.variants(Arm("tariff_1", "MID", "tariff_3"))
    assert variants[0].positions.tolist() == [0, 1, 4]
    assert len(variants) == 8  # Base, two data, three call and two intersections.
    assert all(len(audience.positions) for audience in variants)
    assert all("UNKNOWN" not in audience.filters.values() for audience in variants)
    for audience in variants:
        assert audience.positions.tolist() == index.positions_for(audience.filters).tolist()
        assert audience.positions.tolist() == sorted(audience.positions)
    assert index.variants(Arm("tariff_2", "MID", "tariff_3")) == []


def test_filters_allow_multiple_tariffs_and_do_not_exclude_unfiltered_missing_segments():
    index = SegmentIndex(candidate_profile())
    assert index.positions_for({"filter_current_tariff": " tariff_1 ; tariff_2 "}).tolist() == [
        0,
        1,
        2,
        4,
    ]
    assert index.positions_for(
        {"filter_data_segment": None, "filter_call_segment": np.nan, "target_tariff": "tariff_2"}
    ).tolist() == [0, 1, 2, 3, 4]
    assert index.positions_for({"filter_data_segment": "LITE"}).tolist() == [1, 3]
    with pytest.raises(ValueError, match="UNKNOWN"):
        index.positions_for({"filter_current_tariff": "UNKNOWN"})
    with pytest.raises(ValueError, match="Invalid"):
        index.positions_for({"filter_data_segment": "UNKNOWN"})
    with pytest.raises(ValueError, match="Unsupported"):
        index.positions_for({"filter_customer_id": "10"})


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, -0.01, "not a number"])
def test_index_rejects_invalid_arpu(value):
    profile = candidate_profile()
    profile["predicted_arpu"] = profile["predicted_arpu"].astype(object)
    profile.loc[0, "predicted_arpu"] = value
    with pytest.raises(ValueError, match="finite nonnegative"):
        SegmentIndex(profile)


def test_index_rejects_duplicate_missing_ids_columns_and_unknown_categories():
    profile = candidate_profile()
    profile.loc[0, "ID_NUMBER"] = 10
    with pytest.raises(ValueError, match="unique ID_NUMBER"):
        SegmentIndex(profile)
    profile.loc[0, "ID_NUMBER"] = np.nan
    with pytest.raises(ValueError, match="unique ID_NUMBER"):
        SegmentIndex(profile)
    with pytest.raises(ValueError, match="missing columns"):
        SegmentIndex(candidate_profile().drop(columns=["call_segment"]))
    profile = candidate_profile()
    profile.loc[0, "data_segment"] = "UNKNOWN"
    with pytest.raises(ValueError, match="invalid data_segment"):
        SegmentIndex(profile)


def test_history_only_ranks_and_never_implies_conversion_or_nonzero_priors():
    history = candidate_history(
        [(2000, 6000, "tariff_1", "tariff_2")] * 15
        + [(2000, 2400, "tariff_1", "tariff_3")] * 100
        + [(0, 1e20, "tariff_1", "tariff_4")] * 100
        + [(-1, 1e20, "tariff_1", "tariff_4")]
        + [(np.nan, 1e20, "tariff_1", "tariff_4")]
        + [(2000, np.inf, "tariff_1", "tariff_4")]
        + [(2000, 1e20, "tariff_1", "unknown_tariff")]
    )
    with np.errstate(divide="raise", invalid="raise"):
        candidates = build_candidates(
            SegmentIndex(candidate_profile()), candidate_tariffs(), history
        )
    assert candidates[0].arm.target_tariff == "tariff_2"
    assert (
        candidates[1].arm.target_tariff == "tariff_4"
    )  # An unseen alternative, not count ranking.
    assert "15 valid transitions" in candidates[0].rationale
    assert all(
        candidate.prior_mean == 0 and candidate.prior_variance == 0.25 for candidate in candidates
    )
    assert all(np.isfinite(candidate.priority) for candidate in candidates)


def test_history_segments_use_inclusive_middle_boundaries():
    profile = candidate_profile().iloc[:3].copy()
    profile["current_tariff"] = "tariff_1"
    profile["arpu_segment"] = ["LOW", "MID", "HIGH"]
    history = candidate_history(
        [
            (999, 2997, "tariff_1", "tariff_2"),
            (1000, 3000, "tariff_1", "tariff_3"),
            (5000, 15000, "tariff_1", "tariff_3"),
            (5001, 15003, "tariff_1", "tariff_4"),
        ]
    )
    candidates = build_candidates(SegmentIndex(profile), candidate_tariffs(), history)
    leaders = {}
    for candidate in candidates:
        leaders.setdefault(candidate.arm.arpu_segment, candidate)
    assert leaders["LOW"].arm.target_tariff == "tariff_2"
    assert leaders["MID"].arm.target_tariff == "tariff_3"
    assert "2 valid transitions" in leaders["MID"].rationale
    assert leaders["HIGH"].arm.target_tariff == "tariff_4"


def test_outliers_are_robust_and_order_does_not_change_hypotheses():
    history = candidate_history(
        [(2000, 2200, "tariff_1", "tariff_2")] * 4
        + [(2000, 1e200, "tariff_1", "tariff_2")]
        + [(2000, 4000, "tariff_1", "tariff_3")] * 5
    )
    first = build_candidates(SegmentIndex(candidate_profile()), candidate_tariffs(), history)
    shuffled = build_candidates(
        SegmentIndex(candidate_profile().sample(frac=1, random_state=9)),
        candidate_tariffs().sample(frac=1, random_state=3),
        history.sample(frac=1, random_state=4),
    )
    assert first == shuffled
    assert first[0].arm.target_tariff == "tariff_3"


def test_history_free_candidates_are_distinct_valid_and_limited():
    profile = candidate_profile().iloc[:3].copy()
    profile["current_tariff"] = ["tariff_1", "tariff_2", "tariff_3"]
    profile["arpu_segment"] = ["LOW", "MID", "HIGH"]
    index = SegmentIndex(profile)
    candidates = build_candidates(index, candidate_tariffs(), limit=5)
    assert len(candidates) == 5
    assert len({candidate.arm for candidate in candidates}) == 5
    assert all(
        candidate.arm.current_tariff != candidate.arm.target_tariff for candidate in candidates
    )
    assert build_candidates(index, candidate_tariffs(), pd.DataFrame()) == build_candidates(
        index, candidate_tariffs()
    )
    assert len(build_candidates(index, candidate_tariffs(2))) == 2
    assert build_candidates(index, candidate_tariffs(1)) == []
    assert build_candidates(index, candidate_tariffs(), limit=0) == []


@pytest.mark.parametrize("location", ["kit", "data", "file"])
def test_load_history_reads_only_public_history_from_explicit_location(tmp_path, location):
    kit = tmp_path / "kit.v1"
    data = kit / "data"
    data.mkdir(parents=True)
    path = data / "change_tariff.csv"
    expected = candidate_history([(1000, 2000, "tariff_1", "tariff_2")])
    expected.to_csv(path, index=False)
    source = {"kit": kit, "data": data, "file": path}[location]
    actual = load_history(source)
    pd.testing.assert_frame_equal(actual, expected)
    assert Path(actual.attrs["public_source"]) == path
    assert load_history(tmp_path / "secret_model.csv") is None


def test_load_history_env_is_explicit_and_independent_of_cwd(tmp_path, monkeypatch):
    kit = tmp_path / "kit"
    (kit / "data").mkdir(parents=True)
    candidate_history([(1000, 2000, "tariff_1", "tariff_2")]).to_csv(
        kit / "data" / "change_tariff.csv", index=False
    )
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("PARTICIPANT_KIT_DIR", str(kit))
    monkeypatch.chdir(other)
    actual = load_history()
    assert len(actual) == 1
    monkeypatch.setenv("PARTICIPANT_KIT_DIR", "../kit")
    assert load_history() is None
    monkeypatch.setenv("PARTICIPANT_KIT_DIR", str(other))
    assert load_history() is None


def test_invalid_or_missing_history_has_explicit_fallback(tmp_path):
    path = tmp_path / "data" / "change_tariff.csv"
    path.parent.mkdir()
    path.write_text("secret_effect\n42\n")
    assert load_history(tmp_path) is None
    assert load_history(tmp_path / "nonexistent") is None


@pytest.mark.parametrize("with_history", [False, True])
def test_stratified_shortlist_and_screening_cover_all_arpu_levels(with_history):
    rows, history_rows = [], []
    for number in range(15):
        level = ("LOW", "MID", "HIGH")[number // 5]
        tariff = f"tariff_{number + 1}"
        for _ in range(20):
            rows.append(
                {
                    "ID_NUMBER": len(rows),
                    "current_tariff": tariff,
                    "arpu_segment": level,
                    "data_segment": "LITE",
                    "call_segment": "MEDIUM",
                    "predicted_arpu": {"LOW": 500, "MID": 3000, "HIGH": 9000}[level],
                }
            )
        if level == "LOW":
            # Very large relative historical changes must not monopolize cells.
            history_rows.extend([(1, 1e100, tariff, "tariff_21")] * 100)
    profile = pd.DataFrame(rows)
    history = candidate_history(history_rows) if with_history else None
    tariffs = candidate_tariffs(21)
    index = SegmentIndex(profile)
    candidates = build_candidates(index, tariffs, history, limit=24)
    cells = {(candidate.arm.current_tariff, candidate.arm.arpu_segment) for candidate in candidates}
    assert len(candidates) == 24
    assert Counter(segment for _, segment in cells) == {"LOW": 4, "MID": 4, "HIGH": 4}
    assert {candidate.arm.arpu_segment for candidate in candidates[:3]} == {"LOW", "MID", "HIGH"}
    assert len({candidate.arm.current_tariff for candidate in candidates[:12]}) == 12
    assert Counter(candidate.arm.arpu_segment for candidate in candidates[:8]) == {
        "LOW": 2,
        "MID": 3,
        "HIGH": 3,
    }
    shuffled_history = history.sample(frac=1, random_state=8) if history is not None else None
    assert candidates == build_candidates(
        SegmentIndex(profile.sample(frac=1, random_state=4)),
        tariffs.sample(frac=1, random_state=6),
        shuffled_history,
        limit=24,
    )
    for candidate in candidates[:12]:
        positions = index.cells[candidate.arm.current_tariff, candidate.arm.arpu_segment]
        mass = float(index.arpu[positions[:5000]].sum())
        assert 0.75 * mass <= candidate.priority <= 1.25 * mass
        equivalent_sms = 0.804**2 / (candidate.prior_variance * 0.65**2)
        assert equivalent_sms <= 10
