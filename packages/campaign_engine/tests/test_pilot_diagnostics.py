import copy
import hashlib
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import pytest

from scripts.benchmark_agent import load_public_module
from scripts.diagnose_pilots import (
    SYNTHETIC_FAMILIES,
    SyntheticEvaluator,
    changed_source_files,
    family_comparisons,
    model_summaries,
    pilot_protocol_flags,
    policy_options,
    synthetic_effects,
)


def _inputs():
    tariffs = pd.DataFrame({"tariff_plan_code": ["tariff_1", "tariff_2", "tariff_3"]})
    history = pd.DataFrame({
        "tariff_plan_code_from": ["tariff_1", "tariff_1"],
        "tariff_plan_code_to": ["tariff_2", "tariff_3"],
        "AVG_ARPU_PREV_3M": [2000, 2000],
        "AVG_ARPU_NEXT_3M": [3000, 1000],
    })
    return tariffs, history


@pytest.mark.parametrize("family", SYNTHETIC_FAMILIES)
def test_synthetic_effects_are_fixed_per_model_and_input_order_independent(family):
    tariffs, history = _inputs()
    first = synthetic_effects(tariffs, history, family, 4)
    reordered = synthetic_effects(tariffs.iloc[::-1], history.iloc[::-1], family, 4)
    pd.testing.assert_frame_equal(first, reordered)
    assert not first.equals(synthetic_effects(tariffs, history, family, 5))
    assert first["conversion_rate"].between(0, 1).all()


def test_history_independent_effects_do_not_use_historical_ranking():
    tariffs, history = _inputs()
    pd.testing.assert_frame_equal(
        synthetic_effects(tariffs, history, "history_independent", 0),
        synthetic_effects(tariffs, None, "history_independent", 0),
    )


def test_reversed_family_makes_historical_leader_worse_than_laggard():
    tariffs, history = _inputs()
    effects = synthetic_effects(tariffs, history, "ranking_reversed", 0)
    cell = effects.loc[(effects.tariff_plan_code_from == "tariff_1")
                       & (effects.arpu_segment == "MID")].set_index("tariff_plan_code_to")
    assert cell.loc["tariff_2", "arpu_change_pct"] < 0
    assert cell.loc["tariff_3", "arpu_change_pct"] > 0


def test_diagnostic_options_change_only_selection_and_screening():
    options = policy_options(True)
    base = asdict(options["fixed_200"])
    for option in options.values():
        actual = asdict(option)
        for key in base.keys() - {"policy", "screening_pilots"}:
            assert actual[key] == base[key]
    assert options["adaptive_choice_200"].screening_pilots == 8
    assert options["adaptive_choice_200_screen1"].screening_pilots == 1


def _record(policy, net, *, model_seed=0, seed=0, valid=True, n=200):
    record = {"family": "synthetic", "model_seed": model_seed, "seed": seed,
              "policy": policy, "net_arpu_gain": net, "valid": valid,
              "pilot_requests": [{"n_customers": 200, "channel": "sms"}] * 10,
              "pilot_observations": [{"n_customers": n, "cost": n * 4}] * 10,
              "total_contacts": 4000, "total_cost": 10000, "runtime_seconds": 1.0}
    record["pilot_protocol"] = pilot_protocol_flags(record)
    return record


def test_protocol_flags_distinguish_requested_size_from_executed_size():
    assert _record("fixed_200", 10)["pilot_protocol"]["exact_ten_sms_pilots_of_200"]
    clipped = _record("fixed_200", 10, n=150)["pilot_protocol"]
    assert not clipped["exact_ten_sms_pilots_of_200"]
    assert clipped["pilot_contacts"] == 1500


def test_pairing_uses_model_and_noise_seed_and_retains_invalid_or_uncontrolled_pairs():
    rows = [
        _record("fixed_200", 100), _record("adaptive_choice_200", 130),
        _record("fixed_200", 200, model_seed=1),
        _record("adaptive_choice_200", 180, model_seed=1, n=150),
        _record("fixed_200", 300, seed=1),
        _record("adaptive_choice_200", 500, seed=1, valid=False),
    ]
    comparison = family_comparisons(rows, ["fixed_200", "adaptive_choice_200"])[0]
    assert comparison["attempted_pairs"] == 3
    assert comparison["valid_pairs"] == 2
    assert comparison["controlled_pairs"] == 1
    assert comparison["net_gain_difference"]["mean"] == 5
    assert comparison["controlled_net_gain_difference"]["mean"] == 30
    assert comparison["invalid_or_missing_pairs"] == [(0, 1)]
    assert comparison["protocol_mismatch_pairs"] == [(1, 0)]


@pytest.mark.parametrize("field", ["pilot_requests", "pilot_observations"])
def test_equal_budget_with_different_screening_prefix_is_not_controlled(field):
    baseline = _record("fixed_200", 100)
    candidate = copy.deepcopy(_record("adaptive_choice_200", 130))
    candidate[field][0]["target_tariff"] = "tariff_3"
    comparison = family_comparisons(
        [baseline, candidate], ["fixed_200", "adaptive_choice_200"],
    )[0]
    assert baseline["pilot_protocol"]["exact_ten_sms_pilots_of_200"]
    assert candidate["pilot_protocol"]["exact_ten_sms_pilots_of_200"]
    assert comparison["valid_pairs"] == 1
    assert comparison["controlled_pairs"] == 0
    assert comparison["protocol_mismatch_pairs"] == [(0, 0)]


def test_per_model_variability_does_not_mix_different_model_levels():
    rows = [_record("fixed_200", net, model_seed=model, seed=seed)
            for model, values in [(0, [10, 12]), (1, [1000, 1002])]
            for seed, net in enumerate(values)]
    summaries = model_summaries(rows, ["fixed_200"])
    assert [row["model_seed"] for row in summaries] == [0, 1]
    assert [row["policies"]["fixed_200"]["net_arpu_gain"]["mean"]
            for row in summaries] == [11, 1001]
    for row in summaries:
        assert row["policies"]["fixed_200"]["net_arpu_gain"]["sample_sd"] == pytest.approx(2**0.5)


@pytest.mark.parametrize("group", [
    "sources_sha256", "harness_sources_sha256", "public_tools_sha256", "public_data_sha256",
])
def test_final_hash_check_covers_engine_harness_tools_and_data(tmp_path, group):
    root, kit = tmp_path / "repo", tmp_path / "kit"
    root.mkdir()
    kit.mkdir()
    script = root / "diagnose.py"
    script.write_text("script")
    manifest = {name: {} for name in (
        "sources_sha256", "harness_sources_sha256", "public_tools_sha256", "public_data_sha256",
    )}
    manifest["diagnostic_source_sha256"] = hashlib.sha256(script.read_bytes()).hexdigest()
    base = kit if group.startswith("public_") else root
    source = base / "source.txt"
    source.write_text("before")
    manifest[group][source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
    assert changed_source_files(manifest, root, kit, script) == []
    source.write_text("after")
    assert changed_source_files(manifest, root, kit, script) == [f"{group}:source.txt"]


def test_synthetic_evaluator_charges_repeated_contacts_and_passes_only_env():
    kit = Path(__file__).resolve().parents[3] / "data/participant-kit"
    if not (kit / "environment.py").is_file():
        pytest.skip("Participant data is not installed")
    environment = load_public_module("test_diagnostic_environment", kit / "environment.py")
    scorer = load_public_module("test_diagnostic_scoring", kit / "scoring_core.py")
    profile = pd.DataFrame({
        "ID_NUMBER": [1, 2, 3], "predicted_arpu": [100, 200, 300],
        "current_tariff": ["tariff_1"] * 3, "arpu_segment": ["LOW"] * 3,
        "data_segment": ["LITE"] * 3, "call_segment": ["LOW"] * 3,
    })
    tariffs = pd.DataFrame({"tariff_plan_code": ["tariff_1", "tariff_2"]})
    effects = pd.DataFrame({"tariff_plan_code_from": ["tariff_1"],
                            "tariff_plan_code_to": ["tariff_2"], "arpu_segment": ["LOW"],
                            "arpu_change_pct": [0.4], "conversion_rate": [0.5]})

    class Agent:
        def act(self, env):
            assert not hasattr(env, "effects")
            assert not hasattr(env, "impact_model")
            request = {"target_tariff": "tariff_2", "channel": "sms",
                       "filter_current_tariff": "tariff_1", "filter_arpu_segment": "LOW"}
            env.run_pilot(**request, n_customers=10)
            return [{"campaign_name": "final", **request}]

    result = SyntheticEvaluator(profile, tariffs, effects, environment, scorer).evaluate_agent(
        Agent(), seed=0,
    )
    assert result["total_contacts"] == 6
    assert result["total_cost"] == 24
    assert result["net_arpu_gain"] == pytest.approx(600 * 0.4 * 0.5 * 0.65 - 24)
