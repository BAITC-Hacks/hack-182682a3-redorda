import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from campaign_engine.beliefs import Belief
from campaign_engine.candidates import Arm, Candidate
from campaign_engine.contracts import Campaign
from campaign_engine.engine_types import EngineOptions
from campaign_engine.portfolio import PortfolioBuilder, validate_portfolio
from campaign_engine.segments import SegmentIndex

CHANNELS = {
    "push": {"cost_per_contact": 0, "conversion_multiplier": 0.5},
    "sms": {"cost_per_contact": 4, "conversion_multiplier": 0.65},
    "digital_ads": {"cost_per_contact": 22, "conversion_multiplier": 0.85},
    "call": {"cost_per_contact": 160, "conversion_multiplier": 1.2},
}


def _case(monkeypatch, cells, *, channels=None, **option_overrides):
    rows, candidates, beliefs = [], [], {}
    for number, (arpu, theta) in enumerate(cells, start=1):
        arm = Arm(f"tariff_{number}", "LOW", "tariff_3")
        candidates.append(Candidate(arm))
        beliefs[arm] = Belief(mean=theta)
        for revenue in arpu:
            rows.append({"ID_NUMBER": len(rows) + 1, "predicted_arpu": revenue,
                         "current_tariff": arm.current_tariff, "arpu_segment": "LOW",
                         "data_segment": "LITE", "call_segment": "MEDIUM"})
    profile = pd.DataFrame(rows[::-1])
    calls = []

    def fixed_draws(current_beliefs, count, seed):
        calls.append((count, seed))
        return {arm: np.full(count, belief.mean)
                for arm, belief in current_beliefs.items()}

    monkeypatch.setattr("campaign_engine.portfolio.belief_scenarios", fixed_draws)
    options = EngineOptions(**{"scenario_count": 8, "risk_weight": 0,
                               "portfolio_search": True, **option_overrides})
    index = SegmentIndex(profile)
    channels = CHANNELS if channels is None else channels
    builder = PortfolioBuilder(index, channels, options, seed=7)
    return SimpleNamespace(profile=profile, candidates=candidates, beliefs=beliefs,
                           index=index, channels=channels, options=options,
                           builder=builder, calls=calls)


def _limits(budget, contacts, *, official_budget=None, official_contacts=None):
    return {"budget": budget, "contacts": contacts,
            "official_budget": budget if official_budget is None else official_budget,
            "official_contacts": contacts if official_contacts is None else official_contacts}


def _build(case, limits, **kwargs):
    return case.builder.build(case.candidates, case.beliefs, [], **limits, **kwargs)


def _replay_and_assert(case, result, limits):
    assert 1 <= len(result.campaigns) <= 10
    assert len(result.details) == len(result.campaigns)
    money, reach = limits["official_budget"], limits["official_contacts"]
    spent, contacts, net = 0.0, 0, 0.0
    seen_cells, executed = set(), []
    columns = {"filter_current_tariff": "current_tariff", "filter_arpu_segment": "arpu_segment",
               "filter_data_segment": "data_segment", "filter_call_segment": "call_segment"}
    for campaign, details in zip(result.campaigns, result.details, strict=True):
        Campaign.model_validate(campaign)
        cell = (campaign["filter_current_tariff"], campaign["filter_arpu_segment"])
        assert cell not in seen_cells
        seen_cells.add(cell)
        audience = case.profile
        for name, column in columns.items():
            if name in campaign:
                audience = audience.loc[audience[column] == campaign[name]]
        audience = audience.sort_values("ID_NUMBER", kind="stable")
        channel = case.channels[campaign["channel"]]
        price = channel["cost_per_contact"]
        count = min(len(audience), 5000, reach)
        if price:
            count = min(count, int(money // price))
        assert count > 0
        actual = audience.iloc[:count]
        arpu = float(actual.predicted_arpu.sum())
        arm = Arm(*cell, campaign["target_tariff"])
        theta = case.beliefs[arm].mean
        multiplier = channel["conversion_multiplier"]
        ratio = theta if multiplier > 1 and theta >= 0 else theta * multiplier
        cost = count * price
        incremental_net = arpu * ratio - cost
        assert details["campaign_name"] == campaign["campaign_name"]
        assert details["n_contacts"] == count
        assert details["cost"] == pytest.approx(cost)
        assert details["estimated_incremental_net"] == pytest.approx(incremental_net)
        money -= cost
        reach -= count
        spent += cost
        contacts += count
        net += incremental_net
        executed.append((cell[0], count, arpu))
    assert spent <= limits["budget"]
    assert contacts <= limits["contacts"]
    assert result.cost == pytest.approx(spent)
    assert result.contacts == contacts
    assert result.mean_net == pytest.approx(net)
    assert result.lower_tail_net == pytest.approx(net)
    assert result.objective == pytest.approx(net)
    assert validate_portfolio(result.campaigns, case.index,
                              {"tariff_1", "tariff_2", "tariff_3"}, case.channels,
                              **limits) == {"final_cost": spent, "final_contacts": contacts}
    return executed


def _assert_search_metadata(result, baseline, case):
    metadata = result.search_metadata
    assert 0 <= metadata["accepted"] <= metadata["evaluations"]
    assert metadata["evaluations"] <= case.options.portfolio_search_max_evaluations
    assert metadata["baseline_objective"] == pytest.approx(baseline.objective)
    assert metadata["best_objective"] == pytest.approx(result.objective)
    assert result.objective >= baseline.objective - 1e-8
    assert metadata["elapsed_seconds"] >= 0
    assert isinstance(metadata["termination"], str) and metadata["termination"]


def test_channel_change_refills_other_cells_with_the_freed_budget(monkeypatch):
    case = _case(monkeypatch, [([1000] * 100, 0.20), ([1000] * 100, 0.19)])
    limits = _limits(2200, 200)
    baseline = _build(case, limits)
    assert baseline.mean_net == pytest.approx(24300)
    assert [campaign["channel"] for campaign in baseline.campaigns] == ["digital_ads", "push"]
    result = _build(case, limits, improve=True)
    # SMS/SMS earns 24,550; changing the first channel without refill earns 22,100.
    assert result.objective >= 24550 - 1e-8
    _replay_and_assert(case, result, limits)
    _assert_search_metadata(result, baseline, case)


def test_reordering_reallocates_contact_prefixes(monkeypatch):
    case = _case(monkeypatch, [([1000] * 80, 0.20), ([1400] * 50, 0.20)])
    limits = _limits(2200, 100)
    baseline = _build(case, limits)
    assert baseline.mean_net == pytest.approx(16160)
    assert [details["n_contacts"] for details in baseline.details] == [80, 20]
    result = _build(case, limits, improve=True)
    # Contacting B before A gives 50 contacts to each cell at the same total cost.
    assert result.objective >= 18200 - 1e-8
    _replay_and_assert(case, result, limits)
    _assert_search_metadata(result, baseline, case)


def test_replacement_can_introduce_a_cell_missing_from_the_greedy_portfolio(monkeypatch):
    case = _case(monkeypatch, [([1000] * 100, 0.20), ([1400] * 50, 0.20)])
    limits = _limits(2200, 100)
    baseline = _build(case, limits)
    assert baseline.mean_net == pytest.approx(14800)
    assert len(baseline.campaigns) == 1
    assert baseline.campaigns[0]["filter_current_tariff"] == "tariff_1"
    result = _build(case, limits, improve=True)
    # Changing channels or reordering the sole original campaign cannot introduce B.
    assert result.objective >= 18200 - 1e-8
    assert any(campaign["filter_current_tariff"] == "tariff_2"
               for campaign in result.campaigns)
    _replay_and_assert(case, result, limits)
    _assert_search_metadata(result, baseline, case)


def test_profitable_concentration_in_one_paid_campaign_remains_allowed(monkeypatch):
    case = _case(monkeypatch, [([1000] * 100, 0.60), ([1000] * 100, 0.01)])
    limits = _limits(2200, 200)
    baseline = _build(case, limits)
    assert baseline.mean_net == pytest.approx(49300)
    result = _build(case, limits, improve=True)
    assert result.objective >= 49300 - 1e-8
    assert any(campaign["channel"] == "digital_ads" and details["cost"] == 2200
               for campaign, details in zip(result.campaigns, result.details, strict=True))
    _replay_and_assert(case, result, limits)
    _assert_search_metadata(result, baseline, case)


def test_baseline_and_all_search_neighbors_share_one_scenario_draw(monkeypatch):
    case = _case(monkeypatch, [([1000] * 100, 0.20), ([1000] * 100, 0.19)])
    limits = _limits(2200, 200)
    result = _build(case, limits, improve=True)
    assert case.calls == [(case.options.scenario_count, 7)]
    assert result.search_metadata["evaluations"] > 0
    assert result.search_metadata["accepted"] > 0
    _replay_and_assert(case, result, limits)


def test_search_preserves_the_tail_objective_when_opposite_effects_diversify_risk(monkeypatch):
    case = _case(monkeypatch, [([1000] * 100, 0.20), ([1000] * 100, 0.20)], risk_weight=1)
    scenarios = {
        case.candidates[0].arm: np.array([0.0] * 4 + [0.4] * 4),
        case.candidates[1].arm: np.array([0.4] * 4 + [0.0] * 4),
    }
    monkeypatch.setattr("campaign_engine.portfolio.belief_scenarios",
                        lambda beliefs, count, seed: scenarios)
    limits = _limits(2200, 200)
    baseline = _build(case, limits)
    assert baseline.objective == pytest.approx(20000)
    result = _build(case, limits, improve=True)
    # Each SMS alone has a worst-case loss of 400. Together their opposing effects
    # produce 26,000 gross and 800 cost in every one of the eight scenarios.
    assert result.objective >= 25200 - 1e-8
    outcomes = np.zeros(8)
    for campaign, detail in zip(result.campaigns, result.details, strict=True):
        arm = Arm(campaign["filter_current_tariff"], campaign["filter_arpu_segment"],
                  campaign["target_tariff"])
        multiplier = min(1.0, CHANNELS[campaign["channel"]]["conversion_multiplier"])
        outcomes += scenarios[arm] * multiplier * 1000 * detail["n_contacts"] - detail["cost"]
    assert result.mean_net == pytest.approx(outcomes.mean())
    # Ten percent of eight equally weighted scenarios lies entirely in the minimum.
    assert result.lower_tail_net == pytest.approx(outcomes.min())
    assert result.objective == pytest.approx(outcomes.min())
    assert validate_portfolio(result.campaigns, case.index,
                              {"tariff_1", "tariff_2", "tariff_3"}, case.channels,
                              **limits) == {"final_cost": result.cost,
                                           "final_contacts": result.contacts}
    _assert_search_metadata(result, baseline, case)


def test_reordered_capped_audience_uses_id_prefix_not_top_arpu_or_cell_average(monkeypatch):
    case = _case(monkeypatch, [([900] * 50 + [1300] * 30, 0.20), ([1400] * 50, 0.20)])
    limits = _limits(2200, 100)
    baseline = _build(case, limits)
    assert baseline.mean_net == pytest.approx(16840)
    result = _build(case, limits, improve=True)
    assert result.objective >= 17350 - 1e-8
    executed = _replay_and_assert(case, result, limits)
    # A's first 50 IDs total 45,000; its top 50 total 57,000 and mean-based sum 52,500.
    assert ("tariff_1", 50, 45000) in executed
    _assert_search_metadata(result, baseline, case)


@pytest.mark.parametrize("budget,contacts", [(8, 10), (100, 2)])
def test_search_does_not_turn_smaller_config_limits_into_a_csv_audience_cap(
    monkeypatch, budget, contacts,
):
    case = _case(monkeypatch, [([100] * 3, 1.0), ([100], 0.2)],
                 channels={"sms": CHANNELS["sms"]})
    limits = _limits(budget, contacts, official_budget=100, official_contacts=10)
    baseline = _build(case, limits)
    result = _build(case, limits, improve=True)
    # A would execute all three clients (cost 12), which exceeds the configured limit.
    assert len(result.campaigns) == 1
    assert result.campaigns[0]["filter_current_tariff"] == "tariff_2"
    assert result.contacts == 1
    assert result.cost == 4
    _replay_and_assert(case, result, limits)
    _assert_search_metadata(result, baseline, case)


def test_search_keeps_the_required_nonempty_portfolio_when_all_effects_are_negative(
    monkeypatch,
):
    case = _case(monkeypatch, [([100] * 3, -0.4)])
    limits = _limits(100, 10)
    baseline = _build(case, limits)
    result = _build(case, limits, improve=True)
    assert len(result.campaigns) == 1
    assert result.objective == pytest.approx(-60)
    _replay_and_assert(case, result, limits)
    _assert_search_metadata(result, baseline, case)


@pytest.mark.parametrize("stop", ["deadline", "evaluations", "seconds"])
def test_search_returns_the_exact_checked_baseline_without_available_search_budget(
    monkeypatch, stop,
):
    options = {"portfolio_search_max_evaluations": 0} if stop == "evaluations" else (
        {"portfolio_search_seconds": 0} if stop == "seconds" else {})
    case = _case(monkeypatch, [([1000] * 100, 0.20), ([1000] * 100, 0.19)], **options)
    limits = _limits(2200, 200)
    baseline = _build(case, limits)
    timing = {"deadline": time.monotonic() - 1} if stop == "deadline" else {}
    result = _build(case, limits, improve=True, **timing)
    assert result.campaigns == baseline.campaigns
    assert result.details == baseline.details
    assert result.objective == baseline.objective
    assert result.search_metadata["evaluations"] == 0
    assert result.search_metadata["accepted"] == 0
    _replay_and_assert(case, result, limits)
    _assert_search_metadata(result, baseline, case)
