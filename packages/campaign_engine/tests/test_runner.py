"""Runner state transitions against a public-only, controlled environment."""

import json

import pandas as pd
import pytest
from campaign_engine.contracts import RunConfig
from campaign_engine.engine_types import EngineOptions
from campaign_engine.openai_gateway import PlanningUnavailable
from campaign_engine.runner import run_campaigns


class ControlledEnvironment:
    def __init__(self, *, size=80, money=100_000, contacts=15000):
        self.customer_profile = pd.DataFrame({
            "ID_NUMBER": range(size), "predicted_arpu": [1000.0] * size,
            "current_tariff": ["tariff_1"] * (size // 2) + ["tariff_2"] * (size - size // 2),
            "arpu_segment": ["HIGH"] * size,
            "data_segment": ["HEAVY" if i % 2 else "LITE" for i in range(size)],
            "call_segment": ["LOW" if i % 4 < 2 else "HIGH" for i in range(size)],
        })
        self.tariffs = pd.DataFrame({"tariff_plan_code": ["tariff_1", "tariff_2", "tariff_3"]})
        self.channels = {
            "push": {"cost_per_contact": 0, "conversion_multiplier": 0.5},
            "sms": {"cost_per_contact": 4, "conversion_multiplier": 0.65},
            "digital_ads": {"cost_per_contact": 22, "conversion_multiplier": 0.85},
            "call": {"cost_per_contact": 160, "conversion_multiplier": 1.2},
        }
        self.remaining_budget = money
        self.remaining_contacts = contacts
        self.pilots_left = 20
        self.pilot_history = []
        self.requests = []

    def run_pilot(self, **request):
        self.requests.append(request)
        eligible = self.customer_profile[
            self.customer_profile.current_tariff == request["filter_current_tariff"]]
        n = int(min(len(eligible), request["n_customers"], self.remaining_contacts,
                    self.remaining_budget // 4))
        assert 10 <= request["n_customers"] <= 200
        assert n > 0
        self.remaining_contacts -= n
        self.remaining_budget -= 4 * n
        self.pilots_left -= 1
        response = {"n_customers": n, "cost": 4.0 * n, "observed_lift_ratio": 0.25,
                    "remaining_budget": self.remaining_budget,
                    "remaining_contacts": self.remaining_contacts}
        self.pilot_history.append(response)
        return response


@pytest.fixture
def run_options():
    return EngineOptions(screening_pilots=2, candidate_limit=4, scenario_count=8)


def test_runner_completion_actual_resources_events_and_forecast_are_separate(run_options):
    env = ControlledEnvironment()
    result = run_campaigns(env, RunConfig(max_pilots=3), options=run_options)
    assert result.status == "completed", result.stop_reason
    assert 1 <= len(result.campaigns) <= 10
    assert 1 <= len(result.pilots) <= 3
    assert result.resource_usage["pilot_contacts"] == 15000 - env.remaining_contacts
    assert result.resource_usage["pilot_cost"] == 100_000 - env.remaining_budget
    assert result.resource_usage["total_contacts"] <= 15000
    assert result.resource_usage["total_cost"] <= 100_000
    assert "not_official_score" in result.estimates["source"]
    assert [e["sequence"] for e in result.events] == list(range(1, len(result.events) + 1))
    json.dumps(result.to_dict(), allow_nan=False)
    assert "ID_NUMBER" not in json.dumps(result.to_dict())


def test_default_is_independent_of_api_key_and_never_calls_provider(monkeypatch, run_options):
    def unwanted(*args, **kwargs):
        raise AssertionError("Baseline must never call live OpenAI")

    monkeypatch.setattr("campaign_engine.runner.propose_hypotheses", unwanted)
    a = run_campaigns(ControlledEnvironment(), options=run_options)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    b = run_campaigns(ControlledEnvironment(), options=run_options)
    assert a.status == b.status == "completed"
    assert a.campaigns == b.campaigns
    assert [p.request for p in a.pilots] == [p.request for p in b.pilots]


def test_provider_fallback_completes_and_error_is_redacted(monkeypatch, run_options):
    def unavailable(*args, **kwargs):
        raise PlanningUnavailable("this-private-message-must-not-leak")

    monkeypatch.setattr("campaign_engine.runner.propose_hypotheses", unavailable)
    result = run_campaigns(ControlledEnvironment(), RunConfig(strategy="openai"),
                           options=run_options)
    assert result.status == "completed"
    assert any(e["type"] == "fallback_used" for e in result.events)
    assert "this-private-message" not in json.dumps(result.to_dict())


def test_provider_proposals_are_validated_semantically(run_options):
    def empty_audience(summary):
        return {"hypotheses": [{"campaign": {
            "campaign_name": "bad", "target_tariff": "tariff_3", "channel": "sms",
            "filter_current_tariff": "tariff_21", "filter_arpu_segment": "HIGH",
        }, "rationale": "Not in the public audience"}]}

    result = run_campaigns(ControlledEnvironment(), hypothesis_provider=empty_audience,
                           options=run_options)
    assert result.status == "completed"
    assert result.metadata["hypothesis_source"] == "deterministic"
    assert all(p.request["filter_current_tariff"] != "tariff_21" for p in result.pilots)


def test_cancellation_after_pilot_retains_actual_cost_and_never_repeats(run_options):
    stop = False

    def observe(event):
        nonlocal stop
        if event["type"] == "pilot_completed":
            stop = True

    env = ControlledEnvironment()
    result = run_campaigns(env, options=run_options, observer=observe, should_cancel=lambda: stop)
    assert result.status == "cancelled"
    assert len(result.pilots) == len(env.requests) == 1
    assert result.resource_usage["pilot_cost"] > 0
    assert result.campaigns == []


def test_cancel_before_start_spends_nothing(run_options):
    env = ControlledEnvironment()
    result = run_campaigns(env, options=run_options, should_cancel=lambda: True)
    assert result.status == "cancelled"
    assert env.requests == []


def test_observer_failure_after_pilot_preserves_ledger_and_stops(run_options):
    def broken(event):
        if event["type"] == "pilot_completed":
            raise RuntimeError("Database write failed")

    env = ControlledEnvironment()
    result = run_campaigns(env, options=run_options, observer=broken)
    assert result.status == "failed"
    assert result.stop_reason == "observer_failed_do_not_retry"
    assert len(result.pilots) == len(env.requests) == 1
    assert result.resource_usage["pilot_cost"] > 0


def test_observer_cannot_mutate_internal_event_or_request(run_options):
    def mutate(event):
        event["data"].clear()

    result = run_campaigns(ControlledEnvironment(), options=run_options, observer=mutate)
    assert result.status == "completed"
    assert result.events[0]["data"]["policy"] == "adaptive"


def test_pilot_error_after_mutation_is_not_retried(run_options):
    env = ControlledEnvironment()
    original = env.run_pilot

    def uncertain(**request):
        original(**request)
        raise RuntimeError("Transport lost after payment")

    env.run_pilot = uncertain
    result = run_campaigns(env, options=run_options)
    assert result.status == "failed"
    assert "reconciliation" in result.stop_reason
    assert len(env.requests) == 1


def test_pilot_error_before_mutation_can_try_a_different_arm(run_options):
    env = ControlledEnvironment()
    original = env.run_pilot
    attempted = []

    def fail_once(**request):
        attempted.append(request.copy())
        if len(attempted) == 1:
            raise RuntimeError("Failed before execution")
        return original(**request)

    env.run_pilot = fail_once
    result = run_campaigns(env, options=run_options)
    assert result.status == "completed"
    assert attempted[0] not in attempted[1:]


def test_no_fake_success_for_infeasible_configuration(run_options):
    env = ControlledEnvironment()
    result = run_campaigns(env, RunConfig(max_contacts=1), options=run_options)
    assert result.status == "failed"
    assert not env.requests


def test_smaller_config_budget_and_reach_are_respected(run_options):
    env = ControlledEnvironment()
    config = RunConfig(budget=100, max_contacts=35, max_pilots=2)
    result = run_campaigns(env, config, options=run_options)
    assert result.status == "completed", result.stop_reason
    assert result.resource_usage["total_cost"] <= 100
    assert result.resource_usage["total_contacts"] <= 35
    assert result.resource_usage["final_contacts"] > 0


def test_actual_small_cell_pilot_and_negative_observation(run_options):
    env = ControlledEnvironment(size=6)
    original = env.run_pilot

    def negative(**request):
        response = original(**request)
        response["observed_lift_ratio"] = -0.2
        return response

    env.run_pilot = negative
    result = run_campaigns(env, RunConfig(max_pilots=1), options=run_options)
    assert result.status == "completed", result.stop_reason
    assert result.pilots[0].observation["n_customers"] == 3
    assert result.pilots[0].posterior["mean"] < 0
    assert result.campaigns


def test_no_resume_on_an_environment_with_previous_actions(run_options):
    env = ControlledEnvironment()
    env.pilot_history.append({"n_customers": 100, "cost": 400})
    result = run_campaigns(env, options=run_options)
    assert result.status == "failed"
    assert "fresh environment" in result.stop_reason


def test_expired_deadline_does_not_run_paid_actions(run_options):
    options = EngineOptions(runtime_seconds=0.00001, scenario_count=8)
    env = ControlledEnvironment()
    result = run_campaigns(env, options=options)
    assert result.status == "failed"
    assert env.requests == []
