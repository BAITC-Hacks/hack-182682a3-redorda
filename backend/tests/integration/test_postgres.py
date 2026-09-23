"""Real row-lock tests: SQLite cannot establish these concurrency guarantees."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from apps.campaigns.models import CampaignRun, Dataset
from apps.campaigns.services.execution import begin_run, start_run
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def run(settings):
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    settings.REDORDA_RUN_EXECUTION_ENABLED = True
    settings.REDORDA_ENVIRONMENT_FACTORY = "tests.only.factory"
    dataset = Dataset.objects.create(name="Concurrency", checksum="c" * 64,
                                     source_dir="/unused", customer_count=10)
    return CampaignRun.objects.create(name="Concurrent", dataset=dataset)


@pytest.mark.parametrize("same_key", [True, False])
def test_parallel_http_start_queues_exactly_once(run, monkeypatch, same_key):
    user = get_user_model().objects.create_user(username="parallel")
    barrier = Barrier(8)
    published = []
    lock = Lock()

    def publish(*args):
        with lock:
            published.append(args)

    monkeypatch.setattr("apps.campaigns.services.execution._publish", publish)

    def request(index):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(user=user)
            barrier.wait(timeout=10)
            response = client.post(f"/api/v1/runs/{run.pk}/start/",
                                   HTTP_IDEMPOTENCY_KEY="same" if same_key else f"key-{index}")
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(request, range(8)))
    assert sorted(statuses) == ([202] * 8 if same_key else [202] + [409] * 7)
    assert len(published) == 1
    assert run.events.filter(kind="queued").count() == 1


def test_duplicate_worker_deliveries_have_only_one_claim(run, monkeypatch):
    monkeypatch.setattr("apps.campaigns.services.execution._publish", lambda *_: None)
    queued = start_run(run.pk, idempotency_key="one")
    barrier = Barrier(8)

    def claim(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return begin_run(run.pk, queued.task_id) is not None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(claim, range(8))) == 1
    assert run.events.filter(kind="running").count() == 1
