"""Live queue transport with a runner injected exclusively in tests.

This verifies backend plumbing with an injected test runner.
"""
import csv
import io
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from urllib.parse import quote
from uuid import uuid4

import pytest
from apps.campaigns import tasks
from apps.campaigns.models import CampaignRun, Dataset
from apps.campaigns.services import engine_bridge, execution
from celery.contrib.testing.worker import start_worker
from config.celery import app
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from kombu import pools
from redis import Redis
from rest_framework.test import APIClient

from tests.test_execution import FakeRunner
from tests.test_import import write_kit

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.live]


@pytest.fixture
def live_worker(settings, request):
    if os.getenv("REDORDA_LIVE_TESTS") != "1":
        pytest.skip("Set REDORDA_LIVE_TESTS=1 with PostgreSQL and Redis")
    assert connection.vendor == "postgresql", "Live checks require PostgreSQL"
    queue = f"redorda-test-{uuid4().hex}"
    prefix = f"{queue}:"
    original = {key: app.conf.get(key) for key in (
        "CELERY_TASK_DEFAULT_QUEUE", "CELERY_BROKER_TRANSPORT_OPTIONS",
        "CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS")}
    app.conf.update(CELERY_TASK_DEFAULT_QUEUE=queue,
                    CELERY_BROKER_TRANSPORT_OPTIONS={"global_keyprefix": prefix},
                    CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS={"global_keyprefix": prefix})
    pools.reset()
    app._pool = None
    app.amqp._producer_pool = None
    app.amqp.router = app.amqp.Router()
    assert app.conf.task_default_queue == queue
    settings.REDORDA_RUN_EXECUTION_ENABLED = True
    settings.REDORDA_ENVIRONMENT_FACTORY = "tests.only.factory"
    broker = Redis.from_url(settings.CELERY_BROKER_URL)
    assert broker.ping()
    try:
        worker = (prefork_worker(settings, queue, prefix)
                  if getattr(request, "param", "solo") == "prefork" else
                  start_worker(app, pool="solo", concurrency=1, queues=[queue],
                               perform_ping_check=False, shutdown_timeout=10))
        with worker:
            yield
    finally:
        pools.reset()
        app._pool = None
        app.amqp._producer_pool = None
        app.conf.update(**original)
        app.amqp.router = app.amqp.Router()
        # Never flush the shared Redis database or touch another application's keys.
        keys = list(broker.scan_iter(match=f"{prefix}*"))
        if keys:
            broker.delete(*keys)
        broker.close()


@contextmanager
def prefork_worker(settings, queue, prefix):
    """A separate real worker; its children must use the isolated pytest DB."""
    db = connection.settings_dict
    user, password, name = [quote(str(db.get(key) or ""), safe="")
                            for key in ("USER", "PASSWORD", "NAME")]
    host, port = db.get("HOST") or "localhost", db.get("PORT") or "5432"
    env = {**os.environ, "DATABASE_URL": f"postgresql://{user}:{password}@{host}:{port}/{name}",
           "REDORDA_QUEUE": queue, "REDORDA_REDIS_PREFIX": prefix,
           "REDORDA_ENVIRONMENT_FACTORY": "", "REDORDA_RUN_EXECUTION_ENABLED": "0",
           "DJANGO_DEBUG": "1", "REDIS_URL": settings.CELERY_BROKER_URL}
    env.pop("OPENAI_API_KEY", None)
    hostname = f"{queue}@localhost"
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([
            sys.executable, "-m", "celery", "--workdir", "backend", "-A", "config", "worker",
            "--pool=prefork", "--concurrency=1", f"--hostname={hostname}", "--loglevel=warning",
        ], cwd=settings.ROOT_DIR, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                assert process.poll() is None, "Prefork worker exited during startup"
                if app.control.ping(destination=[hostname], timeout=1):
                    break
            else:
                pytest.fail("Prefork worker did not become ready")
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.mark.parametrize("live_worker", ["prefork"], indirect=True)
def test_separate_prefork_worker_reports_real_missing_engine(client, live_worker):
    run = create(client)
    response = client.post(f"/api/v1/runs/{run.pk}/start/", HTTP_IDEMPOTENCY_KEY="real-worker")
    assert response.status_code == 202
    wait_terminal(run)
    assert (run.status, run.error_code) == ("failed", "engine_unavailable")
    assert not run.pilots.exists() and not run.campaign_results.exists()


@pytest.fixture
def client(tmp_path):
    write_kit(tmp_path, [["1", "12.50", "tariff_1", "LOW", "LITE", "LOW"]])
    call_command("import_participant_data", path=tmp_path)
    api = APIClient()
    api.force_authenticate(user=get_user_model().objects.create_user(username="pipeline"))
    return api


def create(client, seed=42):
    response = client.post("/api/v1/runs/", {"name": "Test-only runner", "seed": seed},
                           format="json")
    assert response.status_code == 201, response.data
    return CampaignRun.objects.get(pk=response.data["id"])


def wait_terminal(run):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        run.refresh_from_db()
        if run.status in execution.TERMINAL:
            return
        time.sleep(0.05)
    pytest.fail(f"Worker did not finish: {run.status}")


def test_import_plan_real_queue_events_results_csv(client, live_worker, monkeypatch):
    def test_engine(run, *, check_cancel):
        return engine_bridge.run_engine(run, check_cancel=check_cancel, runner=FakeRunner())

    monkeypatch.setattr(tasks, "run_engine", test_engine)
    run = create(client)
    url = f"/api/v1/runs/{run.pk}/"
    for _ in range(2):
        assert client.post(url + "start/", HTTP_IDEMPOTENCY_KEY="same").status_code == 202
    wait_terminal(run)
    assert run.status == "completed", run.error_code
    assert run.pilots.count() == 1
    assert run.campaign_results.count() == 1
    events = client.get(url + "events/").data["results"]
    assert [event["kind"] for event in events] == [
        "queued", "running", "pilot_completed", "campaign_result", "result_ready", "completed"]
    assert client.get(url + f"events/?after={events[-1]['id']}").data["results"] == []
    result = client.get(url + "results/")
    assert result.status_code == 200, result.data
    assert result.data["totals"]["pilot_cost"] == "40.00"
    assert result.data["totals"]["predicted_effect"] is None
    exports = [b"".join(client.get(url + "export/").streaming_content) for _ in range(2)]
    assert exports[0] == exports[1]
    rows = list(csv.DictReader(io.StringIO(exports[0].decode("utf-8-sig"))))
    assert len(rows) == 1 and rows[0]["target_tariff"] == "tariff_1"
    assert len(rows[0]) == 7
    assert run.pilots.count() == 1


@pytest.mark.parametrize("mode,expected", [("failure", "execution_failed"),
                                           ("engine", "engine_unavailable"),
                                           ("timeout", "timeout"), ("cancel", "cancelled")])
def test_live_worker_failures_and_cancellation(client, live_worker, monkeypatch, mode, expected):
    def test_engine(run, *, check_cancel):
        if mode == "failure":
            raise RuntimeError("Test worker failure")
        if mode == "timeout":
            raise TimeoutError("Test deadline")
        if mode == "engine":
            # The fixture configures a missing public environment factory.
            return engine_bridge.run_engine(run, check_cancel=check_cancel)

        class CancellingRunner(FakeRunner):
            def act(self, env):
                env.run_pilot(n_customers=10, channel="sms")
                execution.request_cancel(run.pk)
                env.run_pilot(n_customers=10, channel="sms")
        return engine_bridge.run_engine(run, check_cancel=check_cancel, runner=CancellingRunner())

    monkeypatch.setattr(tasks, "run_engine", test_engine)
    run = create(client)
    response = client.post(f"/api/v1/runs/{run.pk}/start/", HTTP_IDEMPOTENCY_KEY=mode)
    run.refresh_from_db()
    assert response.status_code == 202, (response.data, run.error_message)
    wait_terminal(run)
    if mode == "cancel":
        assert run.status == "cancelled"
        assert run.pilots.count() == 1
    else:
        assert (run.status, run.error_code) == ("failed", expected)
    assert client.get(f"/api/v1/runs/{run.pk}/export/").status_code == 409
    assert not run.campaign_results.exists()


def test_dead_redis_is_reported_without_starting_a_worker(settings, client, monkeypatch):
    # A real connection refused error, with retries disabled by the service.
    from kombu import Connection

    settings.REDORDA_RUN_EXECUTION_ENABLED = True
    settings.REDORDA_ENVIRONMENT_FACTORY = "tests.only.factory"
    connection = Connection("redis://127.0.0.1:1/0", connect_timeout=0.2)

    original = tasks.expire_run.apply_async

    def dead_publish(**kwargs):
        return original(connection=connection, **kwargs)

    monkeypatch.setattr(tasks.expire_run, "apply_async", dead_publish)
    run = create(client)
    response = client.post(f"/api/v1/runs/{run.pk}/start/", HTTP_IDEMPOTENCY_KEY="offline")
    assert response.status_code == 503
    run.refresh_from_db()
    assert (run.status, run.error_code) == ("failed", "queue_unavailable")
    assert execution.begin_run(run.pk, run.task_id) is None
    connection.release()


def test_missing_environment_is_explicit(settings):
    settings.REDORDA_ENVIRONMENT_FACTORY = ""
    dataset = Dataset.objects.create(name="No adapter", checksum="d" * 64,
                                     source_dir="/unused", customer_count=1)
    run = CampaignRun.objects.create(name="No adapter", dataset=dataset, status="running")
    with pytest.raises(execution.ExecutionUnavailable, match="not connected"):
        engine_bridge.run_engine(run, check_cancel=lambda: False)
    assert not run.pilots.exists()
