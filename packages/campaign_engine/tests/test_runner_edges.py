"""Adversarial public-response checks: paid actions cannot disappear from the ledger."""

import json

import pytest
from campaign_engine.contracts import RunConfig
from campaign_engine.engine_types import EngineOptions
from campaign_engine.runner import run_campaigns
from campaign_engine.tests.test_runner import ControlledEnvironment


@pytest.fixture
def edge_options():
    return EngineOptions(screening_pilots=1, candidate_limit=4, scenario_count=8)


@pytest.mark.parametrize("failure", ["exception", "invalid_ratio", "invalid_count"])
def test_paid_but_unusable_response_preserves_public_resource_usage(edge_options, failure):
    env = ControlledEnvironment()
    original = env.run_pilot

    def unusable(**request):
        response = original(**request)
        if failure == "exception":
            raise RuntimeError("Response lost after execution")
        if failure == "invalid_ratio":
            response["observed_lift_ratio"] = float("nan")
        else:
            response["n_customers"] += 1
        return response

    env.run_pilot = unusable
    result = run_campaigns(env, options=edge_options)
    assert result.status == "failed"
    assert len(env.requests) == 1
    assert result.resource_usage["pilot_cost"] == 100000 - env.remaining_budget
    assert result.resource_usage["pilot_contacts"] == 15000 - env.remaining_contacts
    assert result.resource_usage["n_pilots"] == 20 - env.pilots_left
    assert result.resource_usage["pilot_cost"] > 0
    # Reconciled resource counters do not invent a valid observation/posterior.
    assert result.pilots == []
    json.dumps(result.to_dict(), allow_nan=False)


def test_finite_observation_that_overflows_posterior_is_not_a_completed_non_json_run(edge_options):
    env = ControlledEnvironment()
    original = env.run_pilot

    def overflowing(**request):
        response = original(**request)
        response["observed_lift_ratio"] = 1e308
        return response

    env.run_pilot = overflowing
    result = run_campaigns(env, RunConfig(max_pilots=1), options=edge_options)
    assert result.status == "failed"
    assert len(env.requests) == 1
    assert result.resource_usage["pilot_cost"] == 100000 - env.remaining_budget
    assert result.campaigns == []
    json.dumps(result.to_dict(), allow_nan=False)


def test_history_mutation_on_failed_pilot_requires_reconciliation_even_with_unchanged_counters(
    edge_options,
):
    env = ControlledEnvironment()
    attempted = []
    original = env.run_pilot

    def partial(**request):
        attempted.append(request)
        if len(attempted) == 1:
            env.pilot_history.append({"pilot": "action_reported_without_settled_counters"})
            raise RuntimeError("Partial public state mutation")
        return original(**request)

    env.run_pilot = partial
    result = run_campaigns(env, options=edge_options)
    assert result.status == "failed"
    assert len(attempted) == 1
    assert "reconciliation" in result.stop_reason


def test_small_reach_without_any_expressible_final_filter_does_not_spend(edge_options):
    env = ControlledEnvironment(size=80)
    # Every base cell has 40 people and every supported refinement has at least 10.
    result = run_campaigns(env, RunConfig(max_contacts=10), options=edge_options)
    assert result.status == "failed"
    assert env.requests == []


def test_cancel_from_pilot_started_observer_is_checked_before_the_paid_call(edge_options):
    env = ControlledEnvironment()
    stop = False

    def observe(event):
        nonlocal stop
        if event["type"] == "pilot_started":
            stop = True

    result = run_campaigns(env, options=edge_options, observer=observe, should_cancel=lambda: stop)
    assert result.status == "cancelled"
    assert env.requests == []


def test_numeric_llm_fields_are_rejected_before_effects_are_used(edge_options):
    env = ControlledEnvironment()

    def invalid_numeric_proposal(summary):
        return {
            "hypotheses": [
                {
                    "campaign": {
                        "campaign_name": "Invalid numeric authority",
                        "target_tariff": "tariff_3",
                        "channel": "sms",
                        "filter_current_tariff": "tariff_1",
                        "filter_arpu_segment": "HIGH",
                        "predicted_gain": float("nan"),
                    },
                    "rationale": "Provider attempts to supply its own arithmetic.",
                }
            ]
        }

    result = run_campaigns(env, hypothesis_provider=invalid_numeric_proposal, options=edge_options)
    assert result.status == "completed", result.stop_reason
    assert result.metadata["hypothesis_source"] == "deterministic"
    assert any(event["type"] == "fallback_used" for event in result.events)
    json.dumps(result.to_dict(), allow_nan=False)


def test_cancel_before_hypothesis_call_does_not_call_external_provider(edge_options):
    env = ControlledEnvironment()
    stop, called = False, False

    def observe(event):
        nonlocal stop
        if event["type"] == "candidates_ready":
            stop = True

    def provider(summary):
        nonlocal called
        called = True
        raise AssertionError("Cancelled run must not call the hypothesis provider")

    result = run_campaigns(
        env,
        options=edge_options,
        observer=observe,
        should_cancel=lambda: stop,
        hypothesis_provider=provider,
    )
    assert result.status == "cancelled"
    assert called is False
    assert env.requests == []
