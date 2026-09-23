"""The run row serializes a cursor with its task projection on PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from apps.campaigns.models import CampaignRun, Dataset, RunEvent
from apps.campaigns.services.team_state import get_team_snapshot, persist_team_event
from django.db import close_old_connections, connection

pytestmark = pytest.mark.django_db(transaction=True)


def test_concurrent_event_and_snapshot_have_matching_cursor(tmp_path):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL transaction test")
    dataset = Dataset.objects.create(name="case", checksum="b" * 64,
                                     source_dir=str(tmp_path), customer_count=1)
    run = CampaignRun.objects.create(name="concurrent", dataset=dataset, status="running")
    barrier = Barrier(2)

    def write_event():
        close_old_connections()
        barrier.wait()
        try:
            return persist_team_event(run_id=run.pk, kind="task_started", payload={
                "task_id": "analysis", "actor_id": "analyst", "status": "running",
                "title": "Analysis", "artifact_ids": [], "evidence_ids": []}).id
        finally:
            connection.close()

    def read_snapshot():
        close_old_connections()
        barrier.wait()
        try:
            return get_team_snapshot(run_id=run.pk)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        event_future = pool.submit(write_event)
        snapshot_future = pool.submit(read_snapshot)
        event_id, snapshot = event_future.result(), snapshot_future.result()
    if snapshot["last_event_id"] >= event_id:
        assert snapshot["tasks"][0]["id"] == "analysis"
    else:
        assert snapshot["tasks"] == []
        assert RunEvent.objects.filter(run=run, id__gt=snapshot["last_event_id"],
                                       id=event_id).exists()
