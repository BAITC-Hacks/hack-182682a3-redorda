"""Real shared runner through the backend; only the public environment is controlled."""

from decimal import Decimal

import pytest
from apps.campaigns.models import CampaignRun, Dataset
from apps.campaigns.services import engine_bridge, execution
from apps.campaigns.services.results import get_results
from apps.campaigns.tasks import execute_run
from campaign_engine.tests.test_runner import ControlledEnvironment
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def configured_run(tmp_path, monkeypatch, settings):
    settings.REDORDA_RUN_EXECUTION_ENABLED = True
    settings.REDORDA_ENVIRONMENT_FACTORY = "tests.only.factory"
    dataset = Dataset.objects.create(name="Controlled public data", checksum="e" * 64,
                                     source_dir=str(tmp_path), customer_count=80)
    env = ControlledEnvironment()
    monkeypatch.setattr(engine_bridge, "_public_environment", lambda context: env)
    monkeypatch.setattr(execution, "_publish", lambda run_id, task_id:
                        execute_run.apply(args=[str(run_id)], task_id=task_id, throw=True))
    run = CampaignRun.objects.create(name="Small web budget", dataset=dataset,
                                     budget=100, max_contacts=35, max_pilots=1, seed=19)
    return run, env


def test_real_runner_uses_web_limits_and_persists_forecasts_and_csv(configured_run):
    run, env = configured_run
    client = APIClient()
    response = client.post(f"/api/v1/runs/{run.pk}/start/", HTTP_IDEMPOTENCY_KEY="real-engine")
    assert response.status_code == 202
    run.refresh_from_db()
    assert run.status == "completed", run.error_message
    result = get_results(run.pk)
    assert Decimal(result["totals"]["total_cost"]) <= 100
    assert result["totals"]["total_contacts"] <= 35
    assert result["totals"]["predicted_effect"]["net_arpu_gain_mean"] is not None
    assert all(Decimal(row["metrics"]["predicted_effect"])
               == Decimal(str(row["metrics"]["estimated_incremental_net"]))
               for row in result["campaigns"])
    assert result["totals"]["simulator_result"] is None
    assert all(row["explanation"] for row in result["campaigns"])
    assert len(env.requests) == run.pilots.count() == 1
    assert run.events.filter(kind="pilot_completed").count() == 1
    started = run.events.get(kind="run_started").payload
    assert started["config"] == {"budget": 100.0, "max_contacts": 35,
                                 "max_pilots": 1, "seed": 19, "strategy": "baseline"}
    assert run.result.summary["engine"]["metadata"]["seed"] == 19
    first = client.get(f"/api/v1/runs/{run.pk}/export/")
    csv = b"".join(first.streaming_content)
    assert b"campaign_name" in csv and b"tariff_" in csv
    assert client.post(f"/api/v1/runs/{run.pk}/start/",
                       HTTP_IDEMPOTENCY_KEY="real-engine").status_code == 202
    assert len(env.requests) == 1
    assert csv == b"".join(client.get(f"/api/v1/runs/{run.pk}/export/").streaming_content)


def test_real_runner_cancel_after_paid_pilot_retains_ledger(configured_run, monkeypatch):
    run, env = configured_run
    original = env.run_pilot

    def cancel_after_pilot(**request):
        result = original(**request)
        execution.request_cancel(run.pk)
        return result

    monkeypatch.setattr(env, "run_pilot", cancel_after_pilot)
    execution.start_run(run.pk, idempotency_key="cancel-real")
    run.refresh_from_db()
    assert run.status == "cancelled"
    assert run.pilots.count() == len(env.requests) == 1
    assert not run.campaign_results.exists()


def test_real_runner_failure_is_not_reported_as_completed(configured_run):
    run, env = configured_run
    run.max_contacts = 1
    run.save()
    execution.start_run(run.pk, idempotency_key="infeasible")
    run.refresh_from_db()
    assert run.status == "failed"
    assert not env.requests
    assert not run.campaign_results.exists()
