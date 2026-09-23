"""Independent diagnostic accounting on synthetic public data and known effect draws."""

import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from campaign_engine.beliefs import Belief
from campaign_engine.candidates import Arm, Candidate
from campaign_engine.engine_types import EngineOptions
from campaign_engine.portfolio import PortfolioBuilder
from campaign_engine.segments import SegmentIndex
from campaign_engine.tests.test_portfolio import _TEST_CHANNELS, _campaign, _pilot, _profile
from campaign_engine.tests.test_runner import ControlledEnvironment

from scripts.benchmark_agent import EngineBenchAgent, evaluate_one
from scripts.diagnose_portfolio import ReplayAgent, check_resources, forecast_plan


@pytest.fixture
def diagnostic_known_draws(monkeypatch):
    def known_draws(beliefs, count, seed):
        return {arm: np.full(count, belief.mean) for arm, belief in beliefs.items()}

    monkeypatch.setattr("campaign_engine.portfolio.belief_scenarios", known_draws)
    monkeypatch.setattr("scripts.diagnose_portfolio.belief_scenarios", known_draws)


@pytest.mark.parametrize("field", ["observed_lift_ratio", "cost", "remaining_contacts"])
def test_replay_rejects_any_changed_public_observation_before_later_pilots(field):
    expected = {
        "observed_lift_ratio": 0.1,
        "n_customers": 200,
        "cost": 800.0,
        "remaining_contacts": 14800,
    }
    actual = {**expected, field: expected[field] + 1}
    requests = []

    def run_pilot(**request):
        requests.append(request)
        return actual

    replay = ReplayAgent([_pilot(200, 800), _pilot(200, 800)], [_campaign()], [expected, expected])
    with pytest.raises(ValueError, match="changed a public observation"):
        replay.act(SimpleNamespace(run_pilot=run_pilot))
    assert len(requests) == 1
    assert replay.matched_observations == 0


def test_replay_preserves_requests_and_returns_an_independent_final_plan():
    pilots = [_pilot(200, 800), _pilot(100, 400, requested=100)]
    observations = [{"n_customers": 200, "cost": 800}, {"n_customers": 100, "cost": 400}]
    requests = []

    def run_pilot(**request):
        requests.append(request)
        return copy.deepcopy(observations[len(requests) - 1])

    plan = [_campaign()]
    replay = ReplayAgent(pilots, plan, observations)
    returned = replay.act(SimpleNamespace(run_pilot=run_pilot))
    assert requests == [pilot.request for pilot in pilots]
    assert replay.matched_observations == 2
    assert returned == plan
    returned[0]["channel"] = "push"
    assert plan[0]["channel"] == "sms"


@pytest.mark.parametrize("field", ["n_contacts", "cost"])
def test_resource_check_rejects_per_row_discrepancy_even_when_totals_match(field):
    forecast = {
        "campaigns": [{"n_contacts": 10, "cost": 40}, {"n_contacts": 20, "cost": 80}],
        "total_contacts": 230,
        "total_cost": 920,
    }
    finals = copy.deepcopy(forecast["campaigns"])
    finals[0][field] += 1
    finals[1][field] -= 1
    record = {
        "campaigns_detail": [{"n_contacts": 200, "cost": 800}, *finals],
        "total_contacts": 230,
        "total_cost": 920,
    }
    with pytest.raises(ValueError, match="campaign's resource use"):
        check_resources(forecast, record, n_pilots=1)


def test_resource_check_matches_final_rows_after_pilots_and_checks_totals():
    forecast = {
        "campaigns": [{"n_contacts": 10, "cost": 40}],
        "total_contacts": 210,
        "total_cost": 840,
    }
    record = {
        "campaigns_detail": [{"n_contacts": 200, "cost": 800}, {"n_contacts": 10, "cost": 40}],
        "total_contacts": 210,
        "total_cost": 840,
    }
    check_resources(forecast, record, n_pilots=1)
    record["total_contacts"] += 1
    with pytest.raises(ValueError, match="total resources"):
        check_resources(forecast, record, n_pilots=1)


@pytest.mark.parametrize(
    "channel, arpu, expected_n, expected_prefix, expected_net",
    [
        ("sms", [10] * 5000 + [1e9, 1e9], 5000, 50000, 12500),
        ("call", [1000] * 625 + [1e8] * 75, 625, 625000, 525000),
    ],
)
def test_forecast_caps_real_id_prefix_and_agrees_with_builder(
    diagnostic_known_draws, channel, arpu, expected_n, expected_prefix, expected_net
):
    # Reverse physical row order; large-ARPU tail must remain outside the ID prefix.
    profile = _profile(arpu).iloc[::-1].reset_index(drop=True)
    index = SegmentIndex(profile)
    arm = Arm("tariff_1", "LOW", "tariff_2")
    candidates, beliefs = [Candidate(arm)], {arm: Belief(mean=1.0)}
    options = EngineOptions(scenario_count=8, risk_weight=0)
    channels = {channel: _TEST_CHANNELS[channel]}
    builder = PortfolioBuilder(index, channels, options, seed=42)
    built = builder.build(
        candidates,
        beliefs,
        [],
        budget=100000,
        contacts=15000,
        official_budget=100000,
        official_contacts=15000,
    )
    forecast = forecast_plan(index, channels, beliefs, [], built.campaigns, options)
    row = forecast["campaigns"][0]
    assert row["full_audience"] == len(arpu)
    assert row["n_contacts"] == expected_n
    assert row["contacted_prefix_arpu"] == expected_prefix
    assert row["full_audience_arpu"] > 100 * expected_prefix
    assert forecast["net_arpu_gain_mean"] == pytest.approx(expected_net)
    assert forecast["net_arpu_gain_mean"] == pytest.approx(built.mean_net)
    assert forecast["objective"] == pytest.approx(built.objective)
    assert forecast["total_contacts"] == built.contacts
    assert forecast["total_cost"] == built.cost


def test_forecast_remaining_contacts_include_pilots_and_prior_final_campaigns(
    diagnostic_known_draws,
):
    first = _profile([1e9] * 5000)
    second = _profile([1e9] * 5000, ids=range(5001, 10001))
    second["current_tariff"] = "tariff_2"
    third = _profile([10] * 4800 + [1e6] * 200, ids=range(10001, 15001))
    third["current_tariff"] = "tariff_3"
    index = SegmentIndex(pd.concat([third, first, second], ignore_index=True))
    candidates = [Candidate(Arm(f"tariff_{i}", "LOW", "tariff_21")) for i in (1, 2, 3)]
    beliefs = {candidate.arm: Belief(mean=1.0) for candidate in candidates}
    pilot = _pilot(200, 800)
    pilot.request["target_tariff"] = "tariff_21"
    options = EngineOptions(scenario_count=8, risk_weight=0)
    channels = {"sms": _TEST_CHANNELS["sms"]}
    builder = PortfolioBuilder(index, channels, options, seed=42)
    built = builder.build(
        candidates,
        beliefs,
        [pilot],
        budget=99200,
        contacts=14800,
        official_budget=99200,
        official_contacts=14800,
    )
    assert built.campaigns[-1]["filter_current_tariff"] == "tariff_3"
    forecast = forecast_plan(index, channels, beliefs, [pilot], built.campaigns, options)
    last = forecast["campaigns"][-1]
    assert last["full_audience"] == 5000
    assert last["n_contacts"] == 4800
    assert last["contacted_prefix_arpu"] == 48000
    assert last["estimated_incremental_net"] == pytest.approx(12000)
    assert forecast["total_contacts"] == 15000
    assert forecast["total_cost"] == 60000
    assert forecast["net_arpu_gain_mean"] == pytest.approx(built.mean_net)
    assert forecast["objective"] == pytest.approx(built.objective)


def test_allowed_channels_restricts_only_the_named_cell_and_preserves_defaults(
    diagnostic_known_draws,
):
    first = _profile([100] * 10)
    second = _profile([100] * 10, ids=range(11, 21))
    second["current_tariff"] = "tariff_3"
    index = SegmentIndex(pd.concat([first, second], ignore_index=True))
    first_arm, second_arm = Arm("tariff_1", "LOW", "tariff_2"), Arm("tariff_3", "LOW", "tariff_4")
    candidates = [Candidate(first_arm), Candidate(second_arm)]
    beliefs = {first_arm: Belief(mean=1.0), second_arm: Belief(mean=1.0)}
    channels = {name: _TEST_CHANNELS[name] for name in ("push", "sms")}
    builder = PortfolioBuilder(index, channels, EngineOptions(scenario_count=8, risk_weight=0), 42)
    resources = dict(budget=100000, contacts=15000, official_budget=100000, official_contacts=15000)
    default = builder.build(candidates, beliefs, [], **resources)
    assert all(campaign["channel"] == "sms" for campaign in default.campaigns)
    assert builder.build(candidates, beliefs, [], allowed_channels=None, **resources) == default
    assert builder.build(candidates, beliefs, [], allowed_channels={}, **resources) == default
    assert (
        builder.build(
            candidates,
            beliefs,
            [],
            allowed_channels={("tariff_1", "LOW"): set(channels)},
            **resources,
        )
        == default
    )
    restricted = builder.build(
        candidates,
        beliefs,
        [],
        allowed_channels={("tariff_1", "LOW"): {"push"}},
        **resources,
    )
    by_cell = {campaign["filter_current_tariff"]: campaign for campaign in restricted.campaigns}
    assert by_cell["tariff_1"]["channel"] == "push"
    assert by_cell["tariff_3"]["channel"] == "sms"
    assert restricted.mean_net == pytest.approx(1000 * 0.5 + 1000 * 0.65 - 40)
    assert builder.build(candidates, beliefs, [], **resources) == default


def test_benchmark_record_preserves_public_observations_resources_and_engine_forecasts(monkeypatch):
    # Exercise the recorder independently of the optional imported organizer kit.
    monkeypatch.setattr(
        "scripts.run_official.validate_agent_result", lambda *args: {"fixture_validation": True}
    )
    env = ControlledEnvironment()
    delegate = EngineBenchAgent("fixed_200", history=None)
    details = [{"campaign_name": "synthetic_scorer_row", "n_contacts": 7, "cost": 28}]

    def synthetic_evaluate(agent, *, seed, verbose):
        agent.act(env)
        usage = delegate.last_result.resource_usage
        return {
            # This explicit test fixture is not a measured organizer score.
            "net_arpu_gain": 0.0,
            "total_cost": usage["total_cost"],
            "total_contacts": usage["total_contacts"],
            "campaigns_detail": details,
        }

    record = evaluate_one(
        SimpleNamespace(evaluate_agent=synthetic_evaluate), delegate, "fixture", 7
    )
    assert record["valid"], record["error"]
    assert record["campaigns_detail"] == details
    assert record["pilot_observations"] == env.pilot_history
    assert record["public_resources"] == {
        "remaining_budget": env.remaining_budget,
        "remaining_contacts": env.remaining_contacts,
        "pilots_left": env.pilots_left,
    }
    assert record["engine_estimates"] == delegate.last_result.estimates
    assert record["pilot_requests"] == [pilot.request for pilot in delegate.last_result.pilots]
    recorded_cost = record["pilot_observations"][0]["cost"]
    env.pilot_history[0]["cost"] = -999
    assert record["pilot_observations"][0]["cost"] == recorded_cost
