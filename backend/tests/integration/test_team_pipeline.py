"""Real HTTP, saved snapshots and Celery/Redis for linked plan commands."""

import time

import pytest
from apps.campaigns.models import CampaignRun, Dataset, TeamCommand
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tests.integration.test_pipeline import live_worker as live_worker

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.live]


def test_snapshot_to_linked_plan_through_real_queue(live_worker):
    dataset = Dataset.objects.create(name="Team queue", checksum="f" * 64,
                                     source_dir="/unused", customer_count=10)
    source = CampaignRun.objects.create(name="Source", dataset=dataset, budget="1000.00")
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user(username="team-queue"))
    base = f"/api/v1/runs/{source.pk}/"
    snapshot = client.get(base + "team/")
    assert snapshot.status_code == 200
    body = {"type": "create_plan", "snapshot_id": snapshot.data["snapshot_id"],
            "parameters": {"name": "Budget alternative", "constraints": {"budget": "500.00"}}}
    first = client.post(base + "commands/", body, format="json", HTTP_IDEMPOTENCY_KEY="plan")
    assert first.status_code == 202, first.data
    command_id = first.data["id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(base + f"commands/{command_id}/")
        assert response.status_code == 200
        if response.data["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert response.data["status"] == "completed", response.data
    repeated = client.post(base + "commands/", body, format="json", HTTP_IDEMPOTENCY_KEY="plan")
    assert repeated.status_code == 202 and repeated.data["id"] == command_id
    plan = CampaignRun.objects.get(pk=response.data["result"]["run_id"])
    assert plan.parent_run_id == source.pk and plan.status == "draft"
    assert str(plan.budget) == "500.00" and plan.dataset_id == dataset.pk
    assert not plan.pilots.exists() and not plan.campaign_results.exists()
    assert source.derived_runs.count() == TeamCommand.objects.filter(run=source).count() == 1
