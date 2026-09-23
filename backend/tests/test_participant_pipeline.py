"""Exercise the installed public kit through HTTP and the real Celery task body."""

import csv
import io
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from apps.campaigns.models import CampaignRun, Dataset
from apps.campaigns.tasks import execute_run
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db(transaction=True)
PARTICIPANT_KIT = Path(__file__).resolve().parents[2] / "data/participant-kit"


@pytest.mark.skipif(not (PARTICIPANT_KIT / "mock_environment.py").is_file(),
                    reason="Participant kit is not installed")
def test_api_worker_public_agent_results_and_export(settings, monkeypatch):
    settings.REDORDA_RUN_EXECUTION_ENABLED = True
    settings.REDORDA_ENVIRONMENT_FACTORY = (
        "apps.campaigns.services.participant_environment.create_environment")
    profile = pd.read_csv(PARTICIPANT_KIT / "customer_profile.csv", usecols=["ID_NUMBER"])
    dataset = Dataset.objects.create(name="Installed public data", checksum="f" * 64,
                                     source_dir=str(PARTICIPANT_KIT), customer_count=len(profile))
    published = []
    monkeypatch.setattr("apps.campaigns.services.execution._publish",
                        lambda run_id, task_id: published.append((run_id, task_id)))

    def unexpected_provider(*args, **kwargs):
        pytest.fail("Baseline must not call OpenAI")

    monkeypatch.setattr("campaign_engine.runner.propose_hypotheses", unexpected_provider)
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user(username="public-pipeline"))
    created = client.post("/api/v1/runs/", {
        "name": "Public kit integration", "strategy": "baseline", "seed": 7,
        "budget": "100000.00", "max_contacts": 15000, "max_pilots": 2,
    }, format="json")
    assert created.status_code == 201, created.data
    run = CampaignRun.objects.get(pk=created.data["id"])
    assert run.dataset_id == dataset.id
    url = f"/api/v1/runs/{run.pk}/"
    for _ in range(2):
        response = client.post(url + "start/", HTTP_IDEMPOTENCY_KEY="public-pipeline")
        assert response.status_code == 202, response.data
    assert len(published) == 1
    run_id, task_id = published[0]
    execute_run.apply(args=[str(run_id)], task_id=task_id, throw=True).get()
    run.refresh_from_db()
    assert run.status == "completed", run.error_code
    assert run.pilots.count() == 2
    assert run.result.summary["metadata"]["seed"] == 7
    assert run.result.summary["metadata"]["strategy"] == "baseline"
    event_page = client.get(url + "events/").data
    event_kinds = [event["kind"] for event in event_page["results"]]
    assert event_kinds.count("pilot_completed") == 2
    assert {"queued", "running", "run_started", "portfolio_updated", "completed"}.issubset(
        event_kinds)
    result = client.get(url + "results/")
    assert result.status_code == 200, result.data
    totals = result.data["totals"]
    assert 0 < Decimal(totals["pilot_cost"]) <= Decimal(totals["total_cost"]) <= 100000
    assert 0 < totals["pilot_contacts"] <= totals["total_contacts"] <= 15000
    assert "forecast" in totals["predicted_effect"]["source"]
    assert totals["predicted_effect"]["net_arpu_gain_mean"] is not None
    assert totals["simulator_result"] is None
    detail = client.get(url).data
    assert detail["progress"]["spent_budget"] == totals["total_cost"]
    assert detail["progress"]["used_contacts"] == totals["total_contacts"]
    download_headers = {"HTTP_ACCEPT": "text/csv, application/json"}
    first = b"".join(client.get(url + "export/", **download_headers).streaming_content)
    second = b"".join(client.get(url + "export/", **download_headers).streaming_content)
    assert first == second and b"OPENAI_API_KEY" not in first and b"sk-proj-" not in first
    rows = list(csv.DictReader(io.StringIO(first.decode("utf-8-sig"))))
    assert len(rows) == len(result.data["campaigns"])
    assert all(len(row) == 7 for row in rows)
    execute_run.apply(args=[str(run_id)], task_id=task_id, throw=True).get()
    assert run.pilots.count() == 2
