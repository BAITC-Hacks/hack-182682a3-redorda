import time
from dataclasses import replace

import pandas as pd
import pytest
from campaign_engine.beliefs import Belief
from campaign_engine.candidates import Arm, Candidate
from campaign_engine.engine_types import EngineOptions, PilotRecord
from campaign_engine.pilots import choose_pilot
from campaign_engine.portfolio import Portfolio
from campaign_engine.segments import SegmentIndex


def pilot_problem(sizes=(400, 400), arpus=(5000.0, 5000.0)):
    rows = []
    for cell, (size, arpu) in enumerate(zip(sizes, arpus, strict=True), start=1):
        start = len(rows)
        rows.extend(
            {
                "ID_NUMBER": start + offset,
                "current_tariff": f"tariff_{cell}",
                "arpu_segment": "MID",
                "data_segment": "LITE",
                "call_segment": "MEDIUM",
                "predicted_arpu": arpu,
            }
            for offset in range(size)
        )
    index = SegmentIndex(pd.DataFrame(rows))
    candidates = [
        Candidate(Arm(f"tariff_{i}", "MID", "tariff_21")) for i in range(1, len(sizes) + 1)
    ]
    beliefs = {candidate.arm: Belief() for candidate in candidates}
    return index, candidates, beliefs


def pilot_arguments(index, candidates, beliefs, **overrides):
    args = dict(
        candidates=candidates,
        beliefs=beliefs,
        index=index,
        channels={"sms": {"cost_per_contact": 4.0, "conversion_multiplier": 0.65}},
        options=EngineOptions(screening_pilots=1),
        successful_pilots=1,
        pilot_contacts=100,
        remaining_budget=9000.0,
        remaining_contacts=3000,
        official_budget=100000.0,
        official_contacts=15000,
        reserve_contacts=20,
        pilots_left=19,
        blocked_arms=set(),
        builder=None,
        pilots=[],
        deadline=time.monotonic() + 30,
    )
    args.update(overrides)
    return args


def test_screening_visits_initial_candidates_once_in_order():
    index, candidates, beliefs = pilot_problem()
    first = candidates[0].arm
    args = pilot_arguments(index, candidates, beliefs, options=EngineOptions(screening_pilots=2))
    choice = choose_pilot(**args)
    assert choice.arm == first
    assert choice.n_requested == choice.n_actual == 100
    assert choice.reason == "Initial broad screening."
    beliefs[first] = Belief(mean=100, variance=0.001, observations=1, customers=100)
    assert choose_pilot(**args).arm == candidates[1].arm


def test_adaptive_can_choose_new_high_value_cell():
    index, candidates, beliefs = pilot_problem()
    first = candidates[0].arm
    beliefs[first] = Belief(mean=0.5, variance=0.000001, observations=4, customers=800)
    choice = choose_pilot(**pilot_arguments(index, candidates, beliefs))
    assert choice.arm == candidates[1].arm
    assert choice.score > 0
    assert 10 <= choice.n_requested <= 200


def test_adaptive_can_repeat_an_uncertain_decision_instead_of_tiny_unseen_cell():
    index, candidates, beliefs = pilot_problem(sizes=(800, 10), arpus=(10000, 10))
    first = candidates[0].arm
    beliefs[first] = Belief(mean=0.01, variance=0.01, observations=1, customers=100)
    choice = choose_pilot(**pilot_arguments(index, candidates, beliefs))
    assert choice.arm == first
    assert choice.n_actual == 200


def test_adaptive_stops_when_information_cannot_change_clear_choices():
    index, candidates, beliefs = pilot_problem()
    for arm in beliefs:
        beliefs[arm] = Belief(mean=0.5, variance=1e-8, observations=4, customers=800)
    assert choose_pilot(**pilot_arguments(index, candidates, beliefs)) is None


def test_competitor_gap_drives_confirmation_of_close_targets():
    index, candidates, beliefs = pilot_problem(sizes=(800,), arpus=(10000,))
    leader = candidates[0].arm
    alternative = Arm("tariff_1", "MID", "tariff_20")
    candidates.append(Candidate(alternative))
    beliefs[leader] = Belief(mean=0.4, variance=0.00001, observations=4, customers=800)
    beliefs[alternative] = Belief(mean=0.39, variance=0.01, observations=1, customers=100)
    choice = choose_pilot(**pilot_arguments(index, candidates, beliefs))
    assert choice.arm == alternative


@pytest.mark.parametrize(
    "policy, requested", [("fixed_100", 100), ("fixed_200", 200), ("wide_100", 100)]
)
def test_fixed_policies_do_not_adapt_to_observed_values(policy, requested):
    index, candidates, beliefs = pilot_problem()
    first, second = [candidate.arm for candidate in candidates]
    options = EngineOptions(policy=policy)
    beliefs[first] = Belief(mean=100, variance=1e-10, observations=1, customers=100)
    args = pilot_arguments(index, candidates, beliefs, options=options)
    choice = choose_pilot(**args)
    assert choice.arm == second
    assert choice.n_requested == requested
    beliefs[first] = replace(beliefs[first], mean=-100, variance=100)
    assert choose_pilot(**args) == choice
    beliefs[second] = Belief(mean=1000, observations=1, customers=100)
    next_choice = choose_pilot(**args)
    if policy == "fixed_100":
        assert next_choice.arm == first
        beliefs[first] = replace(beliefs[first], observations=2)
        beliefs[second] = replace(beliefs[second], observations=2)
        assert choose_pilot(**args) is None
    else:
        assert next_choice is None


def test_blocked_arms_are_never_retried():
    index, candidates, beliefs = pilot_problem()
    args = pilot_arguments(index, candidates, beliefs, blocked_arms={candidates[0].arm})
    assert choose_pilot(**args).arm == candidates[1].arm
    args["blocked_arms"] = set(beliefs)
    assert choose_pilot(**args) is None


def test_smaller_web_budget_cannot_silently_cause_larger_executed_pilot():
    index, candidates, beliefs = pilot_problem()
    args = pilot_arguments(index, candidates, beliefs, remaining_budget=12)
    # The public environment could execute 10, while the web limit permits only 3.
    assert choose_pilot(**args) is None
    args["official_budget"] = 12
    choice = choose_pilot(**args)
    assert choice.n_requested == 10
    assert choice.n_actual == 3


def test_tiny_cell_and_remaining_cap_can_legitimately_execute_fewer_than_ten():
    index, candidates, beliefs = pilot_problem(sizes=(3,), arpus=(1000,))
    choice = choose_pilot(
        **pilot_arguments(index, candidates, beliefs, remaining_budget=12, pilot_contacts=1997)
    )
    assert choice.n_requested == 10
    assert choice.n_actual == 3


def test_contact_reserve_and_pilot_cap_are_enforced_by_requested_size():
    index, candidates, beliefs = pilot_problem()
    choice = choose_pilot(
        **pilot_arguments(index, candidates, beliefs, remaining_contacts=150, reserve_contacts=100)
    )
    assert choice.n_requested == choice.n_actual == 50
    assert (
        choose_pilot(
            **pilot_arguments(
                index, candidates, beliefs, remaining_contacts=109, reserve_contacts=100
            )
        )
        is None
    )
    choice = choose_pilot(**pilot_arguments(index, candidates, beliefs, pilot_contacts=1988))
    assert choice.n_requested == choice.n_actual == 12


@pytest.mark.parametrize(
    "overrides",
    [
        {"pilots_left": 0},
        {"successful_pilots": 20},
        {"pilot_contacts": 2000},
        {"deadline": 0.0},
        {"remaining_contacts": 20, "reserve_contacts": 20},
    ],
)
def test_exhausted_limits_or_deadline_stop_without_calls(overrides):
    index, candidates, beliefs = pilot_problem()
    assert choose_pilot(**pilot_arguments(index, candidates, beliefs, **overrides)) is None


class RecordingLookaheadBuilder:
    def __init__(self, improvement=10, historical_count=0):
        self.calls = []
        self.improvement = improvement
        self.historical_count = historical_count

    def build(self, candidates, beliefs, pilots, **resources):
        self.calls.append((dict(beliefs), list(pilots), resources))
        objective = 100.0 + (self.improvement if len(pilots) > self.historical_count else 0.0)
        # This modeled objective already includes the pilot communication cost.
        return Portfolio(campaigns=[{"campaign_name": "feasible"}], objective=objective)


def test_evolve_updates_beliefs_and_resources_without_double_charging_costs():
    index, candidates, beliefs = pilot_problem()
    first = candidates[0].arm
    beliefs[first] = Belief(mean=0.1, variance=0.01, observations=1, customers=100)
    existing = PilotRecord(
        request={
            "target_tariff": first.target_tariff,
            "channel": "sms",
            "filter_current_tariff": first.current_tariff,
            "filter_arpu_segment": first.arpu_segment,
            "n_customers": 100,
        },
        observation={"n_customers": 100, "cost": 400, "observed_lift_ratio": 0.1},
        prior={},
        posterior=beliefs[first].summary(),
    )
    builder = RecordingLookaheadBuilder(historical_count=1)
    options = EngineOptions(policy="evolve", screening_pilots=1)
    args = pilot_arguments(
        index, candidates, beliefs, options=options, builder=builder, pilots=[existing]
    )
    choice = choose_pilot(**args)
    assert choice is not None
    assert choice.score == pytest.approx(10.0)
    assert len(builder.calls) == 1 + 3 * len(candidates)
    for updated, records, resources in builder.calls[1:]:
        assert len(records) == 2
        assert records[0] is existing
        record = records[-1]
        cost, n = record.observation["cost"], record.observation["n_customers"]
        arm = Arm(
            record.request["filter_current_tariff"],
            record.request["filter_arpu_segment"],
            record.request["target_tariff"],
        )
        assert updated[arm].observations == beliefs[arm].observations + 1
        assert updated[arm].variance < beliefs[arm].variance
        assert resources["budget"] == args["remaining_budget"] - cost
        assert resources["official_budget"] == args["official_budget"] - cost
        assert resources["contacts"] == args["remaining_contacts"] - n
        assert resources["official_contacts"] == args["official_contacts"] - n
    assert beliefs[first].observations == 1
    assert args["pilots"] == [existing]


def test_evolve_stops_when_lookahead_has_no_modeled_value():
    index, candidates, beliefs = pilot_problem()
    beliefs[candidates[0].arm] = Belief(observations=1, customers=100)
    options = EngineOptions(policy="evolve", screening_pilots=1)
    assert (
        choose_pilot(
            **pilot_arguments(
                index,
                candidates,
                beliefs,
                options=options,
                builder=RecordingLookaheadBuilder(improvement=-10),
            )
        )
        is None
    )


def choice_200_schedule(index, candidates, beliefs, *, policy, screening_pilots=8):
    args = pilot_arguments(
        index,
        candidates,
        beliefs,
        options=EngineOptions(policy=policy, screening_pilots=screening_pilots),
        successful_pilots=0,
        pilot_contacts=0,
        remaining_budget=100000.0,
        remaining_contacts=15000,
        official_budget=100000.0,
        official_contacts=15000,
        reserve_contacts=1,
        pilots_left=20,
    )
    choices = []
    for number in range(11):
        action = choose_pilot(**args)
        if action is None:
            break
        choices.append(action)
        # Supplied public observations, independent of any organizer effects.
        ratio = -0.8 if number % 2 else 0.4
        beliefs[action.arm] = beliefs[action.arm].updated(ratio, action.n_actual, 0.65)
        cost = 4 * action.n_actual
        args["successful_pilots"] += 1
        args["pilots_left"] -= 1
        args["pilot_contacts"] += action.n_actual
        args["remaining_budget"] -= cost
        args["official_budget"] -= cost
        args["remaining_contacts"] -= action.n_actual
        args["official_contacts"] -= action.n_actual
    return choices, args


def test_choice_only_policy_has_ten_200_customer_pilots_and_fixed_200_shared_prefix():
    index, candidates, beliefs = pilot_problem(sizes=(400,) * 12, arpus=(5000,) * 12)
    adaptive, adaptive_usage = choice_200_schedule(
        index, candidates, dict(beliefs), policy="adaptive_choice_200"
    )
    fixed, fixed_usage = choice_200_schedule(index, candidates, dict(beliefs), policy="fixed_200")
    assert len(adaptive) == len(fixed) == 10
    assert [action.arm for action in adaptive[:8]] == [action.arm for action in fixed[:8]]
    assert [action.arm for action in adaptive[:8]] == [
        candidate.arm for candidate in candidates[:8]
    ]
    for choices, usage in ((adaptive, adaptive_usage), (fixed, fixed_usage)):
        assert all(action.n_requested == action.n_actual == 200 for action in choices)
        assert usage["pilot_contacts"] == 2000
        assert usage["remaining_budget"] == 92000
        assert usage["remaining_contacts"] == 13000
        assert usage["successful_pilots"] == 10
        assert choose_pilot(**usage) is None


def test_choice_only_ninth_pilot_changes_with_observations_and_can_repeat():
    index, candidates, beliefs = pilot_problem(
        sizes=(800,) + (400,) * 9, arpus=(10000,) + (1000,) * 9
    )
    for candidate in candidates[:8]:
        beliefs[candidate.arm] = beliefs[candidate.arm].updated(0.4, 200, 0.65)
    first = candidates[0].arm
    close_beliefs = {**beliefs, first: Belief().updated(0.0, 200, 0.65)}
    clear_beliefs = {**beliefs, first: Belief().updated(0.8, 200, 0.65)}
    common = dict(
        options=EngineOptions(policy="adaptive_choice_200"),
        successful_pilots=8,
        pilot_contacts=1600,
    )
    confirmation = choose_pilot(**pilot_arguments(index, candidates, close_beliefs, **common))
    exploration = choose_pilot(**pilot_arguments(index, candidates, clear_beliefs, **common))
    assert confirmation.arm == first
    assert "repeated arm" in confirmation.reason
    assert exploration.arm in {candidate.arm for candidate in candidates[8:]}
    assert "new arm" in exploration.reason
    assert confirmation.n_requested == exploration.n_requested == 200


def test_choice_only_does_not_stop_early_for_negative_information_score():
    index, candidates, beliefs = pilot_problem()
    for arm in beliefs:
        beliefs[arm] = Belief(mean=0.5, variance=1e-8, observations=4, customers=800)
    args = pilot_arguments(
        index,
        candidates,
        beliefs,
        options=EngineOptions(policy="adaptive_choice_200"),
        successful_pilots=8,
        pilot_contacts=1600,
    )
    choice = choose_pilot(**args)
    assert choice is not None
    assert choice.n_requested == choice.n_actual == 200
    assert choice.score < 0
    # The existing production policy still stops in this case.
    args["options"] = EngineOptions(policy="adaptive")
    assert choose_pilot(**args) is None


def test_choice_only_reports_actual_small_cells_without_using_more_than_ten_slots():
    index, candidates, beliefs = pilot_problem(sizes=(3,), arpus=(1000,))
    choices, usage = choice_200_schedule(index, candidates, beliefs, policy="adaptive_choice_200")
    assert len(choices) == 10
    assert all(choice.n_requested == 200 and choice.n_actual == 3 for choice in choices)
    assert usage["pilot_contacts"] == 30
    assert usage["remaining_budget"] == 100000 - 120
    assert choose_pilot(**usage) is None


def test_choice_only_cannot_resize_to_fit_a_smaller_local_limit():
    index, candidates, beliefs = pilot_problem()
    args = pilot_arguments(
        index,
        candidates,
        beliefs,
        options=EngineOptions(policy="adaptive_choice_200"),
        remaining_budget=600,
    )
    assert choose_pilot(**args) is None
    args["remaining_budget"] = 9000
    args["remaining_contacts"] = 200
    args["reserve_contacts"] = 20
    assert choose_pilot(**args) is None


def test_choice_only_screening_one_is_an_explicit_separate_ablation():
    index, candidates, beliefs = pilot_problem(sizes=(800, 400), arpus=(10000, 1000))
    first = candidates[0].arm
    beliefs[first] = beliefs[first].updated(0.0, 200, 0.65)
    args = pilot_arguments(
        index,
        candidates,
        beliefs,
        options=EngineOptions(policy="adaptive_choice_200", screening_pilots=1),
        successful_pilots=1,
        pilot_contacts=200,
    )
    assert choose_pilot(**args).arm == first
    args["options"] = EngineOptions(policy="adaptive_choice_200")
    assert choose_pilot(**args).arm == candidates[1].arm
    assert EngineOptions().policy == "adaptive"
