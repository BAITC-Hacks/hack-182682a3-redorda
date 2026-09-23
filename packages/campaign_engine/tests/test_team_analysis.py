"""Saved analysis must neither spend resources nor invent overlapping effects."""

from copy import deepcopy
from importlib import import_module

import pytest


@pytest.fixture
def snapshot():
    return {
        "config": {"budget": "100", "max_contacts": 20},
        "pilots": [{"cost": "20", "n_customers": 2}],
        "evidence_ids": [11, "portfolio-1"],
        "estimates": {"source": "posterior_forecast_not_official_score",
                      "net_arpu_gain_mean": 160, "lower_tail_mean_10": 100,
                      "objective": 130},
        "campaigns": [
            {"id": 1, "campaign": {"channel": "push", "filter_current_tariff": "a",
                                   "filter_arpu_segment": "low"},
             "metrics": {"cost": 30, "n_contacts": 5, "estimated_incremental_net": 70}},
            {"id": 2, "campaign": {"channel": "sms", "filter_current_tariff": "b",
                                   "filter_arpu_segment": "low"},
             "metrics": {"cost": 40, "n_contacts": 5, "estimated_incremental_net": 80}},
        ],
    }


def analysis():
    # Import at call time so the initial failure identifies the missing capability.
    return import_module("campaign_engine.team_analysis")


def test_explanation_uses_saved_forecast_and_evidence(snapshot):
    before = deepcopy(snapshot)
    artifact = analysis().explain(snapshot, "1")
    assert artifact["type"] == "explanation"
    assert artifact["evidence_ids"] == [11, "portfolio-1"]
    assert artifact["data"]["metrics"]["estimated_incremental_net"] == 70
    assert artifact["data"]["simulator_result"] is None
    assert snapshot == before
    with pytest.raises(ValueError, match="campaign"):
        analysis().explain(snapshot, "absent")


def test_budget_compare_preserves_pilot_baseline_and_resources(snapshot):
    before = deepcopy(snapshot)
    artifact = analysis().compare(snapshot, {"budget": "60"})
    alternate = artifact["data"]["alternative"]
    assert alternate["campaign_ids"] == [1]
    assert alternate["total_cost"] == 50
    assert alternate["total_contacts"] == 7
    assert alternate["net_arpu_gain_mean"] == 80  # pilot baseline 10 + marginal 70
    assert alternate["lower_tail_mean_10"] is None
    assert alternate["objective"] is None
    assert artifact["data"]["original"]["net_arpu_gain_mean"] == 160
    assert artifact["data"]["limitations"]
    assert snapshot == before


def test_channels_only_keep_feasible_saved_prefix(snapshot):
    artifact = analysis().compare(snapshot, {"allowed_channels": ["push"]})
    assert artifact["data"]["alternative"]["campaign_ids"] == [1]
    # Once a campaign is removed later saved cohorts may have different cap prefixes.
    with pytest.raises(ValueError, match="No saved campaign"):
        analysis().compare(snapshot, {"allowed_channels": ["sms"]})


def test_unmodified_comparison_preserves_whole_portfolio_risk(snapshot):
    result = analysis().compare(snapshot, {})["data"]
    assert result["alternative"] == result["original"]


def test_compare_rejects_budget_below_spent_pilots(snapshot):
    with pytest.raises(ValueError, match="pilot"):
        analysis().compare(snapshot, {"budget": "19.99"})


def test_compare_does_not_add_overlapping_campaign_effects(snapshot):
    snapshot["campaigns"][1]["campaign"].update(
        filter_current_tariff="a", filter_arpu_segment="low")
    with pytest.raises(ValueError, match="overlap"):
        analysis().compare(snapshot, {"budget": "60"})


def test_explain_accepts_unique_visible_campaign_name(snapshot):
    snapshot["campaigns"][0]["campaign"]["campaign_name"] = "evolve_1_a_b"
    assert analysis().explain(snapshot, "evolve_1_a_b")["data"]["campaign_id"] == 1
    snapshot["campaigns"][1]["campaign"]["campaign_name"] = "evolve_1_a_b"
    with pytest.raises(ValueError, match="campaign"):
        analysis().explain(snapshot, "evolve_1_a_b")


@pytest.mark.parametrize("field,value", [("cost", None), ("estimated_incremental_net", "NaN"),
                                         ("n_contacts", None)])
def test_unknown_metrics_are_not_zero(snapshot, field, value):
    snapshot["campaigns"][0]["metrics"][field] = value
    with pytest.raises(ValueError):
        analysis().compare(snapshot, {"budget": "60"})


def test_compare_accepts_public_digital_ads_channel(snapshot):
    snapshot["campaigns"][0]["campaign"]["channel"] = "digital_ads"
    result = analysis().compare(snapshot, {"allowed_channels": ["digital_ads"]})
    assert result["data"]["alternative"]["campaign_ids"] == [1]


def test_no_saved_campaign_fits_returns_explicit_error(snapshot):
    with pytest.raises(ValueError, match="No saved campaign"):
        analysis().compare(snapshot, {"budget": "25"})
