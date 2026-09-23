import sys
from datetime import datetime, timezone
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from apps.campaigns.models import CampaignResult, CampaignRun, Dataset, Pilot
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def dataset():
    return Dataset.objects.create(name="Test", checksum="a" * 64, source_dir="/unused",
                                  customer_count=12, summary={})


@pytest.fixture
def api_client():
    client = APIClient()
    client.force_authenticate(user=get_user_model().objects.create_user(username=f"api-{uuid4()}"))
    return client


@pytest.fixture
def service_modules(monkeypatch, settings):
    settings.REDORDA_RUN_EXECUTION_ENABLED = True
    settings.REDORDA_ENVIRONMENT_FACTORY = "tests.only.factory"
    package = ModuleType("apps.campaigns.services")
    package.__path__ = []
    execution = ModuleType("apps.campaigns.services.execution")
    results = ModuleType("apps.campaigns.services.results")
    package.execution = execution
    package.results = results
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, execution.__name__, execution)
    monkeypatch.setitem(sys.modules, results.__name__, results)

    class ExecutionConflict(Exception):
        pass

    class ExecutionUnavailable(Exception):
        pass

    class EngineNotReady(Exception):
        pass

    class ResultNotReady(Exception):
        pass

    class InvalidSavedResult(Exception):
        pass

    execution.ExecutionConflict = ExecutionConflict
    execution.ExecutionUnavailable = ExecutionUnavailable
    execution.EngineNotReady = EngineNotReady
    results.ResultNotReady = ResultNotReady
    results.InvalidSavedResult = InvalidSavedResult
    return execution, results


def test_no_dataset_is_an_explicit_empty_state(api_client):
    client = api_client
    assert client.get("/api/v1/datasets/current/").status_code == 404
    response = client.post("/api/v1/runs/", {"name": "October"}, format="json")
    assert response.status_code == 409
    assert response.data["error"]["code"] == "dataset_required"
    assert not CampaignRun.objects.exists()


def test_create_and_retrieve_persisted_draft(dataset, api_client):
    client = api_client
    response = client.post("/api/v1/runs/", {"name": "October", "budget": "50000.50"},
                           format="json")
    assert response.status_code == 201, response.data
    run = CampaignRun.objects.get(pk=response.data["id"])
    assert run.dataset == dataset
    assert run.budget == Decimal("50000.50")
    assert run.status == "draft"
    retrieved = client.get(f"/api/v1/runs/{run.id}/")
    assert retrieved.data["dataset_id"] == str(dataset.id)
    assert retrieved.data["budget"] == "50000.50"
    assert retrieved.data["progress"] == {
        "stage": "draft", "percent": 0, "spent_budget": "0.00",
        "used_contacts": 0, "completed_pilots": 0,
    }
    assert retrieved.data["error"] is None
    assert retrieved.data["cancellation_requested"] is False
    assert client.get("/api/v1/runs/").data["count"] == 1


def test_run_detail_reports_saved_progress_and_safe_failure(dataset, api_client):
    run = CampaignRun.objects.create(name="Plan", dataset=dataset, max_pilots=2,
                                     status="running", started_at=datetime.now(timezone.utc))
    Pilot.objects.create(run=run, sequence=1, request={}, response={},
                         cost=Decimal("40.00"), n_customers=10)
    url = f"/api/v1/runs/{run.id}/"
    running = api_client.get(url).data
    assert running["progress"] == {
        "stage": "running", "percent": 47, "spent_budget": "40.00",
        "used_contacts": 10, "completed_pilots": 1,
    }
    assert running["error"] is None
    campaign = CampaignResult.objects.create(
        run=run, rank=1, campaign={"campaign_name": "Plan", "target_tariff": "tariff_1",
                                   "channel": "sms"},
        metrics={"cost": "12.50", "n_contacts": 25}, explanation="Observed result")
    finalizing = api_client.get(url).data["progress"]
    assert finalizing["stage"] == "finalizing" and finalizing["percent"] == 90
    assert (finalizing["spent_budget"], finalizing["used_contacts"]) == ("52.50", 35)
    campaign.metrics = {}
    campaign.save(update_fields=["metrics"])
    unknown = api_client.get(url).data["progress"]
    assert unknown["spent_budget"] is None and unknown["used_contacts"] is None
    campaign.metrics = {"cost": "12.50", "n_contacts": 25}
    campaign.save(update_fields=["metrics"])
    run.cancel_requested = True
    run.save(update_fields=["cancel_requested"])
    assert api_client.get(url).data["cancellation_requested"] is True
    run.status = "failed"
    run.error_code = "execution_failed"
    run.error_message = "private key and internal traceback"
    run.save(update_fields=["status", "error_code", "error_message"])
    failed = api_client.get(url).data
    assert failed["error"]["code"] == "execution_failed"
    assert "private key" not in str(failed)
    assert failed["progress"]["percent"] == 90
    run.status = "completed"
    run.save(update_fields=["status"])
    completed = api_client.get(url).data
    assert completed["progress"]["percent"] == 100
    assert completed["error"] is None


@pytest.mark.parametrize("field,value", [
    ("budget", "100000.01"), ("budget", "0"), ("max_contacts", 15001),
    ("max_pilots", 21), ("max_pilots", 0), ("seed", -1), ("strategy", "openai"),
])
def test_invalid_run_does_not_get_persisted(dataset, api_client, field, value):
    response = api_client.post("/api/v1/runs/", {"name": "Invalid", field: value}, format="json")
    assert response.status_code == 400
    assert field in response.data["error"]["fields"]
    assert not CampaignRun.objects.exists()


def test_metadata_does_not_advertise_unimplemented_features(api_client):
    response = api_client.get("/api/v1/meta/")
    assert response.data["features"]["run_execution"] is False
    assert response.data["limits"]["contacts"] == 15000


def test_health_remains_public():
    assert APIClient().get("/api/v1/health/").status_code == 200


@pytest.mark.parametrize("payload,field", [
    ({"name": "Invalid", "budget": "0.001"}, "budget"),
    ({"name": "Invalid", "max_contacts": True}, "max_contacts"),
    ({"name": "Invalid", "status": "completed"}, "status"),
])
def test_create_rejects_invalid_input(dataset, api_client, payload, field):
    response = api_client.post("/api/v1/runs/", payload, format="json")
    assert response.status_code == 400
    assert response.data["error"]["code"] == "validation_error"
    assert field in response.data["error"]["fields"]
    assert not CampaignRun.objects.exists()


def test_start_requires_key_and_reuses_same_service_result(dataset, api_client, service_modules):
    execution, _ = service_modules
    run = CampaignRun.objects.create(name="Plan", dataset=dataset)
    seen = []

    def start_run(run_id, *, idempotency_key, execution_available):
        seen.append((run_id, idempotency_key, execution_available))
        run.status = "queued"
        return run

    execution.start_run = start_run
    url = f"/api/v1/runs/{run.id}/start/"
    missing = api_client.post(url)
    assert missing.status_code == 400
    assert "Idempotency-Key" in missing.data["error"]["fields"]
    for _ in range(2):
        response = api_client.post(url, HTTP_IDEMPOTENCY_KEY="same-key")
        assert response.status_code == 202
        assert response.data["status"] == "queued"
    assert seen == [(run.id, "same-key", True), (run.id, "same-key", True)]


def test_same_key_http_retry_survives_execution_flag_change(dataset, api_client, settings,
                                                              monkeypatch):
    settings.REDORDA_RUN_EXECUTION_ENABLED = True
    settings.REDORDA_ENVIRONMENT_FACTORY = "tests.only.factory"
    monkeypatch.setattr("apps.campaigns.services.execution._publish", lambda *_: None)
    run = CampaignRun.objects.create(name="Plan", dataset=dataset)
    url = f"/api/v1/runs/{run.id}/start/"
    first = api_client.post(url, HTTP_IDEMPOTENCY_KEY="same-key")
    assert first.status_code == 202
    settings.REDORDA_RUN_EXECUTION_ENABLED = False
    retry = api_client.post(url, HTTP_IDEMPOTENCY_KEY="same-key")
    assert retry.status_code == 202
    assert retry.data["status"] == "queued"
    assert api_client.post(url, HTTP_IDEMPOTENCY_KEY="different").status_code == 409


def test_start_conflict_unavailable_and_missing_run(dataset, api_client, service_modules):
    execution, _ = service_modules
    run = CampaignRun.objects.create(name="Plan", dataset=dataset)
    url = f"/api/v1/runs/{run.id}/start/"
    for error, expected in [(execution.ExecutionConflict, 409),
                            (execution.ExecutionUnavailable, 503)]:
        def raise_error(*args, **kwargs):
            raise error("private details")

        execution.start_run = raise_error
        response = api_client.post(url, HTTP_IDEMPOTENCY_KEY="key")
        assert response.status_code == expected
        assert "private details" not in str(response.data)
    missing = api_client.post(f"/api/v1/runs/{uuid4()}/start/", HTTP_IDEMPOTENCY_KEY="key")
    assert missing.status_code == 404
    assert set(missing.data["error"]) == {"code", "message", "fields"}


@pytest.mark.parametrize("method,suffix", [
    ("get", ""), ("post", "start/"), ("post", "cancel/"),
    ("get", "events/"), ("get", "results/"), ("get", "export/"),
])
def test_unknown_run_returns_404(api_client, method, suffix):
    url = f"/api/v1/runs/{uuid4()}/{suffix}"
    response = getattr(api_client, method)(url, HTTP_IDEMPOTENCY_KEY="key")
    assert response.status_code == 404
    assert response.data["error"]["code"] == "not_found"


def test_cancel_is_idempotent(dataset, api_client, service_modules):
    execution, _ = service_modules
    run = CampaignRun.objects.create(name="Plan", dataset=dataset, status="queued")
    calls = []

    def request_cancel(run_id):
        calls.append(run_id)
        run.status = "cancelled"
        return run

    execution.request_cancel = request_cancel
    for _ in range(2):
        response = api_client.post(f"/api/v1/runs/{run.id}/cancel/")
        assert response.status_code == 200
        assert response.data["status"] == "cancelled"
    assert calls == [run.id, run.id]


def test_events_cursor_pagination_and_empty_page(dataset, api_client, monkeypatch):
    run = CampaignRun.objects.create(name="Plan", dataset=dataset)
    from apps.campaigns import views

    now = datetime.now(timezone.utc)
    events = [SimpleNamespace(id=i, kind="progress",
                              payload={"step": i, "task_id": "internal"}, created_at=now)
              for i in range(1, 4)]

    class Query(list):
        def filter(self, **kwargs):
            return Query(item for item in self if item.id > kwargs["id__gt"])

        def order_by(self, key):
            assert key == "id"
            return Query(sorted(self, key=lambda item: item.id))

    monkeypatch.setattr(views.apps, "get_model",
                        lambda *args: SimpleNamespace(objects=Query(events)))
    url = f"/api/v1/runs/{run.id}/events/"
    page = api_client.get(url, {"after": 0, "limit": 2})
    assert page.status_code == 200
    assert [event["id"] for event in page.data["results"]] == [1, 2]
    assert page.data["results"][0]["payload"] == {"step": 1}
    assert page.data["next_after"] == 2
    assert page.data["has_more"] is True
    empty = api_client.get(url, {"after": 3})
    assert empty.data == {"results": [], "next_after": 3, "has_more": False}
    huge = 2**63
    assert api_client.get(url, {"after": huge}).data == {
        "results": [], "next_after": huge, "has_more": False,
    }
    for query in [{"after": -1}, {"after": "abc"}, {"limit": 0}, {"limit": 201}]:
        invalid = api_client.get(url, query)
        assert invalid.status_code == 400
        assert invalid.data["error"]["code"] == "validation_error"


def test_results_and_export_status(dataset, api_client, service_modules):
    _, results = service_modules
    run = CampaignRun.objects.create(name="Plan", dataset=dataset)
    url = f"/api/v1/runs/{run.id}"

    def unavailable(_run_id):
        raise results.ResultNotReady("internal status")

    results.get_results = unavailable
    assert api_client.get(f"{url}/results/").status_code == 409
    assert api_client.get(f"{url}/export/").status_code == 409

    run.status = "completed"
    run.save(update_fields=["status"])
    results.get_results = lambda _run_id: {"run_id": run.id,
                                            "totals": {"total_cost": Decimal("12.50")}}
    results.iter_submission_csv = lambda _run_id: iter(["campaign_name\n", "Plan\n"])
    response = api_client.get(f"{url}/results/")
    assert response.status_code == 200
    assert response.data["totals"]["total_cost"] == "12.50"
    exported = api_client.get(f"{url}/export/")
    assert exported.status_code == 200
    assert b"".join(exported.streaming_content) == b"campaign_name\nPlan\n"
