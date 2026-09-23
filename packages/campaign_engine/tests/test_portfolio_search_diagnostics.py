"""Checks for paired portfolio diagnostics, including rejected and losing attempts."""

import contextlib
import copy
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from campaign_engine.engine_types import EngineOptions, PilotRecord

from scripts import compare_portfolio_search as comparison


def fixed_observations():
    pilots, public = [], []
    for number in range(10):
        observation = {"n_customers": 200, "cost": 800.0, "observed_lift_ratio": number / 100}
        pilots.append(PilotRecord({"channel": "sms", "n_customers": 200},
                                  copy.deepcopy(observation), {}, {}))
        public.append(observation)
    return pilots, public


def test_snapshot_disables_search_and_isolates_engine_randomness():
    options = EngineOptions(policy="adaptive", candidate_limit=18, scenario_count=16,
                            risk_weight=0.2, portfolio_search=True,
                            portfolio_search_max_evaluations=17)
    agent = comparison.FrozenSnapshotAgent(None, options, engine_seed=71)
    assert agent.config.seed == 71 and agent.config.strategy == "baseline"
    assert agent.options.policy == "fixed_200"
    assert agent.options.portfolio_search is False
    assert agent.options.pilot_contact_cap == 2000
    assert agent.options.candidate_limit == 18 and agent.options.scenario_count == 16
    assert agent.options.risk_weight == 0.2
    assert agent.options.portfolio_search_max_evaluations == 17


def test_exact_fixed_pilot_schedule_is_accepted():
    comparison.validate_fixed_schedule(*fixed_observations())


@pytest.mark.parametrize("field,value,reason", [
    ("channel", "push", "200 SMS"), ("n_customers", 100, "200 SMS"),
])
def test_control_rejects_a_changed_pilot_request(field, value, reason):
    pilots, public = fixed_observations()
    pilots[3].request[field] = value
    with pytest.raises(ValueError, match=reason):
        comparison.validate_fixed_schedule(pilots, public)


@pytest.mark.parametrize("target", ["pilot", "public"])
def test_control_rejects_capped_actual_pilots(target):
    pilots, public = fixed_observations()
    observation = pilots[2].observation if target == "pilot" else public[2]
    observation["n_customers"] = 199
    with pytest.raises(ValueError, match="capped audience"):
        comparison.validate_fixed_schedule(pilots, public)


@pytest.mark.parametrize("field,value,reason", [
    ("cost", 799, "cost differs"), ("observed_lift_ratio", 0.1234, "public observation"),
])
def test_control_rejects_changed_public_cost_or_observation(field, value, reason):
    pilots, public = fixed_observations()
    public[0][field] = value
    with pytest.raises(ValueError, match=reason):
        comparison.validate_fixed_schedule(pilots, public)


def test_control_requires_exactly_ten_successful_pilots():
    pilots, public = fixed_observations()
    with pytest.raises(ValueError, match="exactly 10"):
        comparison.validate_fixed_schedule(pilots[:9], public[:9])


def known_forecast():
    details = [{"n_contacts": 300, "cost": 1200, "estimated_incremental_net": 500.0},
               {"n_contacts": 200, "cost": 800, "estimated_incremental_net": 500.0}]
    return ({"net_arpu_gain_mean": 1000.0, "objective": 900.0,
             "total_cost": 10000, "total_contacts": 2500,
             "campaigns": details},
            SimpleNamespace(mean_net=1000.0, objective=900.0, cost=2000, contacts=500,
                            details=copy.deepcopy(details)))


def test_valuation_checks_include_already_spent_pilot_resources():
    forecast, portfolio = known_forecast()
    comparison.check_valuation(portfolio, forecast, fixed_observations()[0])
    portfolio.cost += 1
    with pytest.raises(ValueError, match="portfolio resources"):
        comparison.check_valuation(portfolio, forecast, fixed_observations()[0])


@pytest.mark.parametrize("field", ["cost", "n_contacts", "estimated_incremental_net"])
def test_equal_totals_cannot_hide_misassigned_campaign_metrics(field):
    forecast, portfolio = known_forecast()
    portfolio.details[0][field] += 1
    portfolio.details[1][field] -= 1
    with pytest.raises(ValueError, match="campaign metrics"):
        comparison.check_valuation(portfolio, forecast, fixed_observations()[0])


def test_changed_objective_is_rejected_even_if_expected_mean_matches():
    forecast, portfolio = known_forecast()
    portfolio.objective += 1
    with pytest.raises(ValueError, match="portfolio valuation"):
        comparison.check_valuation(portfolio, forecast, fixed_observations()[0])


def test_invalid_snapshot_keeps_partial_score_and_failure_stage(monkeypatch):
    partial = {"valid": False, "error": "Pilot response was invalid", "net_arpu_gain": -99.0}
    monkeypatch.setattr(comparison, "evaluate_one", lambda *args: partial)
    record = comparison.compare_seed(None, None, 13)
    assert record["valid"] is False
    assert record["failure_stage"] == "snapshot"
    assert record["snapshot_score"] == partial
    assert "Pilot response was invalid" in record["error"]
    assert record["variants"] == {}


def test_summary_keeps_losing_pairs_and_reports_invalid_denominator():
    valid = {"environment_seed": 0, "valid": True, "actual_net_change": -20,
             "forecast_objective_change": 10,
             "variants": {"greedy": {"scored": {"net_arpu_gain": 100}, "build_seconds": 1.0,
                                     "candidate_evaluations": 0},
                          "search": {"scored": {"net_arpu_gain": 80}, "build_seconds": 2.0,
                                     "candidate_evaluations": 10}}}
    invalid = {"environment_seed": 1, "valid": False}
    summary = comparison.summarize([valid, invalid])
    assert (summary["attempted_pairs"], summary["valid_pairs"], summary["invalid_pairs"]) == (
        2, 1, 1)
    assert summary["invalid_seeds"] == [1]
    assert summary["actual_net_change"]["mean"] == -20
    assert summary["forecast_objective_change"]["mean"] == 10
    assert (summary["wins"], summary["ties"], summary["losses"]) == (0, 0, 1)
    assert summary["variants"]["search"]["actual_net"]["mean"] == 80
    assert summary["actual_net_change"]["sample_sd"] is None


def test_summary_with_no_valid_pairs_does_not_report_zero_gain():
    summary = comparison.summarize([{"environment_seed": 9, "valid": False}])
    assert summary["valid_pairs"] == 0 and summary["invalid_seeds"] == [9]
    assert summary["actual_net_change"]["mean"] is None
    assert summary["variants"]["search"]["actual_net"]["mean"] is None


def test_installed_kit_replays_both_plans_on_identical_pilots(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    kit = root / "data/participant-kit"
    if not (kit / "local_eval.py").is_file():
        pytest.skip("Participant kit is not installed")
    monkeypatch.syspath_prepend(str(kit))
    # Scope relative imports so another test's evaluator cannot select a different dataset.
    for name in ("environment", "scoring_core", "mock_environment"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location(
        "paired_search_public_eval", kit / "local_eval.py")
    evaluator = importlib.util.module_from_spec(spec)
    with contextlib.chdir(kit):
        spec.loader.exec_module(evaluator)
        options = EngineOptions(portfolio_search_max_evaluations=8, portfolio_search_seconds=10.0)
        record = comparison.compare_seed(evaluator, comparison.load_history(kit), 42,
                                         options=options, engine_seed=19)
    assert record["valid"], (record["failure_stage"], record["error"])
    assert record["snapshot"]["config"]["seed"] == 19
    assert record["snapshot"]["options"]["portfolio_search"] is False
    assert len(record["snapshot"]["pilots"]) == 10
    original = record["snapshot"]["public_observations"]
    for variant in record["variants"].values():
        assert variant["matching_pilot_observations"] == 10
        assert variant["scored"]["pilot_observations"] == original
        assert variant["resource_accounting_matches"] is True
        assert variant["forecast"]["total_cost"] == variant["scored"]["total_cost"]
        assert variant["forecast"]["total_contacts"] == variant["scored"]["total_contacts"]
    assert record["forecast_objective_change"] >= -1e-6
    assert 0 <= record["variants"]["search"]["candidate_evaluations"] <= 8
