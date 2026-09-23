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
