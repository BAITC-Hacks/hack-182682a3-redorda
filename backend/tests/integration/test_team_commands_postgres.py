"""PostgreSQL row-lock guarantees for command submission."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from apps.campaigns import models
from apps.campaigns.services import team_commands
from django.db import close_old_connections, connection, connections

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ready(monkeypatch):
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    if not all(hasattr(models, name) for name in ("TeamCommand", "TeamSnapshot")):
        pytest.skip("Team models from backend-data are not integrated yet")
    from apps.campaigns.services import team_state

    dataset = models.Dataset.objects.create(name="Team concurrency", checksum="d" * 64,
                                            source_dir="/unused", customer_count=10)
    run = models.CampaignRun.objects.create(name="Source", dataset=dataset)
    snapshot = models.TeamSnapshot.objects.create(run=run, schema_version=1, engine_state={})
    monkeypatch.setattr(team_state, "load_snapshot", lambda **_: snapshot)
    published = []
    lock = Lock()

    def publish(command_id):
        with lock:
            published.append(command_id)

    monkeypatch.setattr(team_commands, "_publish", publish)
    return run, snapshot, published


@pytest.mark.parametrize("same_body", [True, False])
def test_parallel_submissions_create_one_command(ready, same_body):
    run, snapshot, published = ready
    barrier = Barrier(8)

    def submit(index):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                result = team_commands.submit_command(
                    run_id=run.pk, command_type="explain", snapshot_id=str(snapshot.pk),
                    parameters={"campaign_id": "same" if same_body else str(index)},
                    idempotency_key="parallel")
                return result["id"]
            except team_commands.CommandConflict:
                return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(submit, range(8)))
    ids = {item for item in results if item != "conflict"}
    assert len(ids) == 1
    assert results.count("conflict") == (0 if same_body else 7)
    assert models.TeamCommand.objects.filter(run=run).count() == len(published) == 1


def test_parallel_worker_claims_start_only_one_task(ready):
    run, snapshot, _ = ready
    command = models.TeamCommand.objects.create(
        run=run, snapshot=snapshot, type="explain", parameters={"campaign_id": "1"},
        idempotency_key="claim", request_hash="a" * 64)
    barrier = Barrier(8)

    def claim(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return team_commands._claim(command.pk) is not None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(claim, range(8))) == 1
    assert models.TeamTask.objects.filter(run=run).count() == 1
    assert run.events.filter(kind="task_started").count() == 1


def test_timeout_and_artifact_completion_are_atomic(ready):
    run, snapshot, _ = ready
    command = models.TeamCommand.objects.create(
        run=run, snapshot=snapshot, type="explain", parameters={"campaign_id": "1"},
        idempotency_key="finish", request_hash="a" * 64)
    team_commands._claim(command.pk)
    barrier = Barrier(2)
    artifact = {"type": "explanation", "title": "Saved observations", "data": {},
                "evidence_ids": []}

    def finish(expire):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            if expire:
                team_commands.expire_command(command.pk)
            else:
                team_commands._artifact_result(command, "explanation", artifact)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(finish, [False, True]))
    command.refresh_from_db()
    task = models.TeamTask.objects.get(run=run)
    if command.status == "completed":
        assert command.error is None and task.status == "completed"
        assert models.TeamArtifact.objects.filter(run=run).count() == 1
        assert command.result["id"] == models.TeamArtifact.objects.get(run=run).id
    else:
        assert command.status == task.status == "failed"
        assert command.error["code"] == "timeout" and command.result is None
        assert not models.TeamArtifact.objects.filter(run=run).exists()
