from itertools import combinations, product

import numpy as np
import pandas as pd
import pytest
from campaign_engine.beliefs import PILOT_NOISE_SD, Belief, effect_ratio
from campaign_engine.candidates import Arm, Candidate
from campaign_engine.engine_types import EngineOptions, PilotRecord
from campaign_engine.portfolio import (
    PortfolioBuilder,
    expected_best_ratio,
    portfolio_objective,
    validate_portfolio,
)
from campaign_engine.segments import SegmentIndex

_TEST_CHANNELS = {
    "push": {"cost_per_contact": 0, "conversion_multiplier": 0.5},
    "sms": {"cost_per_contact": 4, "conversion_multiplier": 0.65},
    "digital_ads": {"cost_per_contact": 22, "conversion_multiplier": 0.85},
    "call": {"cost_per_contact": 160, "conversion_multiplier": 1.2},
}


def _profile(arpu, *, ids=None, data=None):
    count = len(arpu)
    return pd.DataFrame({
        "ID_NUMBER": list(range(1, count + 1)) if ids is None else ids,
        "predicted_arpu": arpu,
        "current_tariff": ["tariff_1"] * count,
        "arpu_segment": ["LOW"] * count,
        "data_segment": ["LITE"] * count if data is None else data,
        "call_segment": ["MEDIUM"] * count,
    })


def _exact_portfolio(monkeypatch, profile, *, channels=None, mean=1.0):
    # Known effects isolate executable audience/cost mechanics from statistical sampling.
    monkeypatch.setattr(
        "campaign_engine.portfolio.belief_scenarios",
        lambda beliefs, count, seed: {
            arm: np.full(count, belief.mean) for arm, belief in beliefs.items()
        },
    )
    index = SegmentIndex(profile)
    arm = Arm("tariff_1", "LOW", "tariff_2")
    candidates, beliefs = [Candidate(arm)], {arm: Belief(mean=mean)}
    channels = {"sms": _TEST_CHANNELS["sms"]} if channels is None else channels
    builder = PortfolioBuilder(index, channels, EngineOptions(scenario_count=8, risk_weight=0), 7)
    return index, builder, candidates, beliefs


def _campaign(name="final", channel="sms"):
    return {
        "campaign_name": name,
        "filter_current_tariff": "tariff_1",
        "filter_arpu_segment": "LOW",
        "target_tariff": "tariff_2",
        "channel": channel,
    }


def _pilot(n_actual, cost, *, requested=200, channel="sms"):
    request = {key: value for key, value in _campaign(channel=channel).items()
               if key != "campaign_name"}
    request["n_customers"] = requested
    return PilotRecord(request, {"n_customers": n_actual, "cost": cost}, {}, {})


def _enumerate_independent_contacts(effects, probabilities):
    result = np.zeros(len(effects[0]))
    for happened in product([False, True], repeat=len(effects)):
        probability = np.prod([
            p if event else 1 - p for p, event in zip(probabilities, happened, strict=True)
        ])
        selected = [effect for effect, event in zip(effects, happened, strict=True) if event]
        # Zero is used only when nobody contacted this client, never as a floor on loss.
        if selected:
            result += probability * np.max(selected, axis=0)
    return result


@pytest.mark.parametrize("effects,probabilities", [
    ([[-0.4, 0.8]], [0.25]),
    ([[-0.4, 0.8], [-0.2, -0.3]], [0.5, 0.25]),
    ([[-0.4, 0.8], [-0.2, -0.3], [-0.1, 0.2]], [0.5, 0.25, 1.0]),
    ([[0.3, -0.8], [0.3, -0.8], [-0.2, -0.4]], [0.0, 0.75, 0.5]),
])
def test_expected_best_matches_complete_contact_event_enumeration(effects, probabilities):
    arrays = [np.array(effect) for effect in effects]
    actual = expected_best_ratio(arrays, probabilities, len(arrays[0]))
    np.testing.assert_allclose(actual, _enumerate_independent_contacts(arrays, probabilities))


def test_negative_contacts_do_not_become_zero_and_no_contact_is_zero():
    # No contact: 3/8; only the worse campaign: 3/8; better campaign: 1/4.
    # Thus -0.4 * 3/8 - 0.2 * 1/4 = -0.2, not zero or a conditional average.
    observed = expected_best_ratio([np.array([-0.4]), np.array([-0.2])], [0.5, 0.25], 1)
    assert observed[0] == pytest.approx(-0.2)
    np.testing.assert_array_equal(expected_best_ratio([], [], 3), np.zeros(3))


def test_repeated_pilot_probabilities_match_sampling_without_replacement():
    arpu = np.array([10.0, 30.0, 80.0])
    effects = [np.array([-0.4, 0.2]), np.array([-0.1, -0.3])]
    # First pilot draws one of three, second draws two of three. Draws are
    # without replacement within each pilot and independent between pilots.
    outcomes = []
    for first, second in product(combinations(range(3), 1), combinations(range(3), 2)):
        revenue = np.zeros(2)
        for customer in range(3):
            contacted = []
            if customer in first:
                contacted.append(effects[0])
            if customer in second:
                contacted.append(effects[1])
            if contacted:
                revenue += arpu[customer] * np.max(contacted, axis=0)
        outcomes.append(revenue)
    predicted = arpu.sum() * expected_best_ratio(effects, [1 / 3, 2 / 3], 2)
    np.testing.assert_allclose(predicted, np.mean(outcomes, axis=0))


def test_final_campaign_uses_id_prefix_instead_of_largest_arpu(monkeypatch):
    _, builder, candidates, beliefs = _exact_portfolio(
        monkeypatch, _profile([1000, 10, 20], ids=[3, 1, 2]),
    )
    result = builder.build(candidates, beliefs, [], budget=100, contacts=2,
                           official_budget=100, official_contacts=2)
    assert result.contacts == 2
    assert result.cost == 8
    assert result.mean_net == pytest.approx((10 + 20) * 0.65 - 8)


def test_official_budget_caps_exact_prefix_and_rounds_down_contacts(monkeypatch):
    _, builder, candidates, beliefs = _exact_portfolio(monkeypatch, _profile([10, 20, 1000]))
    result = builder.build(candidates, beliefs, [], budget=10, contacts=20,
                           official_budget=10, official_contacts=20)
    assert result.contacts == 2
    assert result.cost == 8
    assert result.mean_net == pytest.approx(11.5)


@pytest.mark.parametrize("budget,contacts", [(100, 2), (8, 10)])
def test_smaller_config_cannot_invent_an_unexportable_audience_cap(monkeypatch, budget, contacts):
    index, builder, candidates, beliefs = _exact_portfolio(monkeypatch, _profile([10, 20, 30]))
    result = builder.build(candidates, beliefs, [], budget=budget, contacts=contacts,
                           official_budget=100, official_contacts=10)
    assert result.campaigns == []
    with pytest.raises(ValueError, match="configured resources"):
        validate_portfolio([_campaign()], index, {"tariff_1", "tariff_2"}, _TEST_CHANNELS,
                           budget=budget, contacts=contacts, official_budget=100,
                           official_contacts=10)


def test_smaller_config_uses_an_expressible_segment_if_available(monkeypatch):
    index, builder, candidates, beliefs = _exact_portfolio(
        monkeypatch, _profile([100, 100, 100], data=["LITE", "HEAVY", "HEAVY"]),
    )
    result = builder.build(candidates, beliefs, [], budget=4, contacts=1,
                           official_budget=100, official_contacts=10)
    assert result.contacts == 1
    assert result.campaigns[0]["filter_data_segment"] == "LITE"
    assert "n_customers" not in result.campaigns[0]
    resources = validate_portfolio(result.campaigns, index, {"tariff_1", "tariff_2"},
                                   _TEST_CHANNELS, budget=4, contacts=1,
                                   official_budget=100, official_contacts=10)
    assert resources == {"final_cost": 4, "final_contacts": 1}


def test_campaign_contact_cap_is_five_thousand(monkeypatch):
    _, builder, candidates, beliefs = _exact_portfolio(
        monkeypatch, _profile([1] * 5001), channels={"push": _TEST_CHANNELS["push"]},
    )
    result = builder.build(candidates, beliefs, [], budget=100, contacts=15000,
                           official_budget=100, official_contacts=15000)
    assert result.contacts == 5000
    assert result.cost == 0
    assert result.mean_net == 2500


def test_repeated_pilot_and_final_costs_are_paid_even_when_effect_is_deduplicated(monkeypatch):
    _, builder, candidates, beliefs = _exact_portfolio(monkeypatch, _profile([10, 20, 30]))
    pilots = [_pilot(2, 8), _pilot(2, 8)]
    result = builder.build(candidates, beliefs, pilots, budget=84, contacts=2,
                           official_budget=84, official_contacts=2)
    # First two clients receive the final campaign for sure; the third was
    # contacted by at least one of two pilots with probability 1 - (1/3)^2.
    gross = (10 + 20) * 0.65 + 30 * 0.65 * (8 / 9)
    assert result.cost == 8  # Final cost; 16 of pilot cost also enters mean_net.
    assert result.contacts == 2
    assert result.mean_net == pytest.approx(gross - 8 - 8 - 8)


def test_guard_counts_every_duplicate_contact_and_replays_remaining_budget():
    index = SegmentIndex(_profile([10, 20, 30]))
    repeated = [_campaign("first"), _campaign("second")]
    resources = validate_portfolio(repeated, index, {"tariff_1", "tariff_2"}, _TEST_CHANNELS,
                                   budget=22, contacts=6, official_budget=22, official_contacts=6)
    # First campaign contacts all three (12), second contacts an ID prefix of
    # two (8). Those repeated contacts still cost money and consume the limit.
    assert resources == {"final_cost": 20, "final_contacts": 5}
    with pytest.raises(ValueError, match="configured resources"):
        validate_portfolio(repeated, index, {"tariff_1", "tariff_2"}, _TEST_CHANNELS,
                           budget=22, contacts=3, official_budget=22, official_contacts=6)


@pytest.mark.parametrize("mean,expected", [(-0.4, -20), (0.4, 20)])
def test_pilot_only_forecast_uses_actual_sample_size_and_preserves_losses(
    monkeypatch, mean, expected,
):
    _, builder, _, beliefs = _exact_portfolio(
        monkeypatch, _profile([100, 100]), channels={"push": _TEST_CHANNELS["push"]}, mean=mean,
    )
    result = builder.build([], beliefs, [_pilot(1, 0, channel="push")], budget=100,
                           contacts=10, official_budget=100, official_contacts=10,
                           force_nonempty=False)
    assert result.mean_net == pytest.approx(expected)


@pytest.mark.parametrize("theta", [-0.4, 0.0, 0.4])
@pytest.mark.parametrize("conversion", [0.1, 0.5, 0.9, 1.0])
def test_call_forecast_is_a_lower_bound_under_conversion_saturation(theta, conversion):
    conditional_change = theta / conversion
    actual_call = conditional_change * min(1.2 * conversion, 1.0)
    conservative_call = float(effect_ratio(theta, "call", _TEST_CHANNELS))
    assert conservative_call <= actual_call + 1e-12
    assert conservative_call == pytest.approx(theta if theta >= 0 else 1.2 * theta)


def test_unsaturated_channels_scale_public_multiplier_exactly():
    theta = np.array([-0.4, 0.0, 0.4])
    for channel, multiplier in [("push", 0.5), ("sms", 0.65), ("digital_ads", 0.85)]:
        np.testing.assert_allclose(effect_ratio(theta, channel, _TEST_CHANNELS), theta * multiplier)


def test_bayesian_update_weights_actual_customers_and_does_not_clip_negative_observations():
    # The prior carries one unit of precision. Three actual customers (even
    # if a pilot requested 200) contribute three units, so (-.2 + 3*.6)/4=.4.
    prior = Belief(mean=-0.2, variance=PILOT_NOISE_SD**2)
    posterior = prior.updated(ratio=0.6, n_actual=3, multiplier=1.0)
    assert posterior.mean == pytest.approx(0.4)
    assert posterior.variance == pytest.approx(PILOT_NOISE_SD**2 / 4)
    assert posterior.customers == 3
    assert posterior.observations == 1
    negative = Belief(variance=PILOT_NOISE_SD**2).updated(-1.2, 1, 1.0)
    assert negative.mean == pytest.approx(-0.6)
    assert prior.customers == 0


def test_bayesian_update_accounts_for_pilot_channel_multiplier():
    # Choose a prior with the same precision as an SMS pilot of four people.
    prior_variance = PILOT_NOISE_SD**2 / (4 * 0.65**2)
    posterior = Belief(mean=0.1, variance=prior_variance).updated(0.39, 4, 0.65)
    assert posterior.mean == pytest.approx(0.35)  # Midpoint of .1 and .39/.65 = .6.
    assert posterior.variance == pytest.approx(prior_variance / 2)


def test_repeated_bayesian_updates_agree_with_pooled_sufficient_statistics():
    prior = Belief(mean=0.1, variance=PILOT_NOISE_SD**2)
    repeated = prior.updated(0.2, 2, 1.0).updated(0.6, 3, 1.0)
    pooled = prior.updated(0.44, 5, 1.0)
    assert repeated.mean == pytest.approx(2.3 / 6)
    assert repeated.mean == pytest.approx(pooled.mean)
    assert repeated.variance == pytest.approx(pooled.variance)
    assert repeated.customers == 5
    assert repeated.observations == 2


@pytest.mark.parametrize("n_actual", [0, -1, 1.5, float("nan"), float("inf"), True])
def test_bayesian_update_rejects_invalid_actual_counts(n_actual):
    with pytest.raises(ValueError, match="Invalid pilot observation"):
        Belief().updated(0.2, n_actual, 0.65)


@pytest.mark.parametrize("mean", [float("nan"), float("inf"), -float("inf")])
def test_bayesian_update_rejects_nonfinite_prior_mean(mean):
    with pytest.raises(ValueError, match="Invalid prior"):
        Belief(mean=mean).updated(0.2, 10, 0.65)


def test_lower_tail_preserves_fractional_probability_mass():
    samples = np.arange(12, dtype=float)
    # Exactly 10% of 12 equally weighted outcomes means one full observation
    # and 0.2 of the next, rather than rounding to one or two observations.
    assert portfolio_objective(samples, 1.0) == pytest.approx(1 / 6)
    assert portfolio_objective(samples, 0.0) == pytest.approx(5.5)
