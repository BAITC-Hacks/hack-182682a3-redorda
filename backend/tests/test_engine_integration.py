import json
from decimal import Decimal

import pytest
from apps.campaigns.models import CampaignRun, Dataset, RunEvent
from apps.campaigns.services import engine_bridge
from apps.campaigns.services.engine_bridge import ExecutionCancelled, run_engine
from apps.campaigns.services.execution import cancellation_requested
from apps.campaigns.services.results import get_results
from campaign_engine.engine_types import EngineFailure
from campaign_engine.tests.test_runner import ControlledEnvironment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def calculation(tmp_path, monkeypatch):
    dataset = Dataset.objects.create(name="Public data", checksum="e" * 64,
                                     source_dir=str(tmp_path), customer_count=80)
    run = CampaignRun.objects.create(name="Connected agent", dataset=dataset, status="running",
                                     budget=1000, max_contacts=100, max_pilots=2, seed=123,
                                     task_id="worker-task")
    environment = ControlledEnvironment(money=1000, contacts=100)
    contexts, history_paths = [], []

    def environment_factory(context):
        contexts.append(context)
        return environment

    def history(path):
        history_paths.append(path)
        return None

    monkeypatch.setattr(engine_bridge, "_public_environment", environment_factory)
    monkeypatch.setattr(engine_bridge, "load_history", history)
    return run, environment, contexts, history_paths


def test_shared_runner_uses_saved_settings_and_persists_forecast(calculation):
    run, environment, contexts, history_paths = calculation
    run_engine(run, check_cancel=lambda: False)
    context = contexts[0]
    assert (context.budget, context.max_contacts, context.max_pilots, context.seed,
            context.strategy) == (1000, 100, 2, 123, "baseline")
    assert history_paths == [context.dataset_path]
    assert len(environment.requests) == run.pilots.count() == 2
    summary = run.result.summary
    assert summary["metadata"]["seed"] == 123
    assert summary["metadata"]["strategy"] == "baseline"
    assert summary["metadata"]["hypothesis_source"] == "deterministic"
    assert summary["resource_usage"]["total_contacts"] <= 100
    assert summary["resource_usage"]["total_cost"] <= 1000
    assert "forecast" in summary["predicted_effect"]["source"]
    assert summary["simulator_result"] is None
    events = list(run.events.all())
    assert sum(event.kind == "pilot_completed" for event in events) == 2
    assert {"run_started", "candidates_ready", "portfolio_updated", "run_completed",
            "result_ready"}.issubset({event.kind for event in events})
    pilot_event = next(event for event in events if event.kind == "pilot_completed")
    assert "observation" in pilot_event.payload and "request" in pilot_event.payload
    assert run.events.filter(kind="pilot_estimate_updated").count() == 2
    run.status = "completed"
    run.save(update_fields=["status"])
    output = get_results(run.pk)
    assert output["totals"]["total_contacts"] == summary["resource_usage"]["total_contacts"]
    assert Decimal(output["totals"]["total_cost"]) == summary["resource_usage"]["total_cost"]
    for campaign in output["campaigns"]:
        assert campaign["metrics"]["n_contacts"] > 0
        assert campaign["metrics"]["cost"] is not None
        assert "estimated_incremental_net" in campaign["metrics"]


def test_openai_strategy_invokes_provider_and_keeps_its_forecast_separate(calculation, monkeypatch):
    run, _, _, _ = calculation
    run.strategy = "openai"
    run.save(update_fields=["strategy"])
    summaries = []

    def provider(summary, **kwargs):
        summaries.append(summary)
        return {"hypotheses": [{"campaign": {
            "campaign_name": "Target hypothesis", "target_tariff": "tariff_3", "channel": "sms",
            "filter_current_tariff": "tariff_1", "filter_arpu_segment": "HIGH",
        }, "rationale": "Test aggregate hypothesis"}]}

    monkeypatch.setattr("campaign_engine.runner.propose_hypotheses", provider)
    run_engine(run, check_cancel=lambda: False)
    assert len(summaries) == 1
    assert "ID_NUMBER" not in json.dumps(summaries)
    assert run.result.summary["metadata"]["hypothesis_source"] == "openai"
    assert run.events.filter(kind="hypotheses_ready").count() == 1
    assert run.result.summary["simulator_result"] is None


def test_provider_failure_falls_back_without_exposing_provider_message(calculation, monkeypatch):
    run, _, _, _ = calculation
    run.strategy = "openai"
    run.save(update_fields=["strategy"])

    def unavailable(*args, **kwargs):
        raise RuntimeError("sensitive-provider-detail")

    monkeypatch.setattr("campaign_engine.runner.propose_hypotheses", unavailable)
    run_engine(run, check_cancel=lambda: False)
    assert run.result.summary["metadata"]["hypothesis_source"] == "deterministic"
    assert run.events.filter(kind="fallback_used").count() == 1
    assert "sensitive-provider-detail" not in json.dumps(run.result.summary)
    assert "sensitive-provider-detail" not in json.dumps(list(run.events.values("payload")))


def test_cancel_during_pilot_saves_spend_once_and_no_final_result(calculation, monkeypatch):
    run, environment, _, _ = calculation
    public_pilot = environment.run_pilot

    def cancel_after_response(**request):
        response = public_pilot(**request)
        CampaignRun.objects.filter(pk=run.pk).update(cancel_requested=True)
        return response

    monkeypatch.setattr(environment, "run_pilot", cancel_after_response)
    with pytest.raises(ExecutionCancelled):
        run_engine(run, check_cancel=lambda: cancellation_requested(run.pk, run.task_id))
    assert len(environment.requests) == run.pilots.count() == 1
    assert run.events.filter(kind="pilot_completed").count() == 1
    assert not run.campaign_results.exists()
    assert not hasattr(run, "result")


def test_event_persistence_failure_does_not_repeat_a_paid_pilot(calculation, monkeypatch):
    run, environment, _, _ = calculation
    create = RunEvent.objects.create

    def fail_after_pilot(**values):
        if values["kind"] == "portfolio_updated":
            raise RuntimeError("storage unavailable")
        return create(**values)

    monkeypatch.setattr(RunEvent.objects, "create", fail_after_pilot)
    with pytest.raises(EngineFailure, match="observer_failed_do_not_retry"):
        run_engine(run, check_cancel=lambda: False)
    assert len(environment.requests) == run.pilots.count() == 1
    assert not run.campaign_results.exists()
    assert not hasattr(run, "result")


def test_observation_events_are_immutable_and_cursor_gets_later_estimate(calculation, monkeypatch):
    run, _, _, _ = calculation
    create = RunEvent.objects.create
    snapshots = []

    def capture_observation(**values):
        event = create(**values)
        if event.kind == "pilot_completed":
            snapshots.append((event.id, json.loads(json.dumps(event.payload))))
        return event

    monkeypatch.setattr(RunEvent.objects, "create", capture_observation)
    run_engine(run, check_cancel=lambda: False)
    for cursor, original in snapshots:
        assert RunEvent.objects.get(pk=cursor).payload == original
        assert "observed_lift_ratio" in original["observation"]
        later = run.events.filter(id__gt=cursor, kind="pilot_estimate_updated").first()
        assert later.payload["sequence"] == original["sequence"]
        assert "posterior" in later.payload


def test_tiny_audience_uses_actual_pilot_count_before_budget_check(calculation, monkeypatch):
    run, _, _, _ = calculation
    run.budget, run.max_contacts, run.max_pilots = Decimal(32), 8, 1
    run.save(update_fields=["budget", "max_contacts", "max_pilots"])
    environment = ControlledEnvironment(size=8, money=32, contacts=8)
    monkeypatch.setattr(engine_bridge, "_public_environment", lambda context: environment)
    run_engine(run, check_cancel=lambda: False)
    pilot = run.pilots.get()
    assert pilot.request["n_customers"] == 10
    assert pilot.n_customers == 4
    assert pilot.cost == 16
    assert run.result.summary["resource_usage"]["total_cost"] <= 32
