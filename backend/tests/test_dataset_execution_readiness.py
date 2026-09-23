import pytest
from apps.campaigns.models import CampaignRun, Dataset
from apps.campaigns.services import execution
from apps.campaigns.services.public_environment import LOCAL_FACTORY
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def published(monkeypatch, settings):
    settings.REDORDA_ENVIRONMENT_FACTORY = LOCAL_FACTORY
    monkeypatch.setattr("apps.campaigns.views.execution_enabled", lambda: True)
    tasks = []
    monkeypatch.setattr(execution, "_publish", lambda *args: tasks.append(args))
    return tasks


def test_raw_csv_draft_explains_missing_profile_and_never_queues(published):
    dataset = Dataset.objects.create(name="Four CSVs", checksum="a" * 64,
                                     source_dir="/raw", customer_count=1,
                                     summary={"format": "raw_csv"})
    run = CampaignRun.objects.create(name="Raw draft", dataset=dataset)
    client = APIClient()
    url = f"/api/v1/runs/{run.id}/"
    response = client.get(url)
    assert "customer_profile.csv" in response.data["execution_blocker"]
    response = client.post(url + "start/", HTTP_IDEMPOTENCY_KEY="raw")
    assert response.status_code == 503
    assert "customer_profile.csv" in response.data["error"]["message"]
    run.refresh_from_db()
    assert run.status == "draft"
    assert run.events.count() == 0
    assert published == []


def test_full_dataset_starts_and_idempotent_retry_ignores_later_readiness_change(published):
    dataset = Dataset.objects.create(name="Full kit", checksum="b" * 64, source_dir="/kit",
                                     customer_count=1)
    run = CampaignRun.objects.create(name="Ready", dataset=dataset)
    first = execution.start_run(run.pk, idempotency_key="same")
    assert first.status == "queued"
    assert len(published) == 1
    dataset.summary = {"format": "raw_csv"}
    dataset.save()
    assert execution.start_run(run.pk, idempotency_key="same",
                               execution_available=False).task_id == first.task_id
    assert len(published) == 1
