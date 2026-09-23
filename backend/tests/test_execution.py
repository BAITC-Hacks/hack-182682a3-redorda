from decimal import Decimal

import pytest
from apps.campaigns import models

if not hasattr(models, "RunEvent"):
    pytest.skip("Execution models are supplied by the backend-data branch", allow_module_level=True)

from apps.campaigns.models import CampaignResult, CampaignRun, Dataset, Pilot, RunEvent, RunResult
from apps.campaigns.services.engine_bridge import ExecutionCancelled, run_engine
from apps.campaigns.services.execution import (
    ExecutionConflict,
    ExecutionUnavailable,
    begin_run,
    cancellation_requested,
    finish_run,
    request_cancel,
    start_run,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def run():
    dataset = Dataset.objects.create(name="Test", checksum="b" * 64, source_dir="/unused",
                                     customer_count=12, summary={})
    return CampaignRun.objects.create(name="Test run", dataset=dataset)


def test_start_is_idempotent_and_published_once(run, monkeypatch):
    published = []
    monkeypatch.setattr("apps.campaigns.services.execution._publish",
                        lambda run_id, task_id: published.append((run_id, task_id)))
    first = start_run(run.pk, idempotency_key="one")
    second = start_run(run.pk, idempotency_key="one")
    assert first.task_id == second.task_id
    assert first.status == "queued"
    assert len(published) == 1
    assert list(RunEvent.objects.values_list("kind", flat=True)) == ["queued"]
    with pytest.raises(ExecutionConflict):
        start_run(run.pk, idempotency_key="different")


def test_unavailable_broker_is_terminal_and_late_delivery_is_ignored(run, monkeypatch):
    monkeypatch.setattr("apps.campaigns.tasks.expire_run.apply_async", lambda **kwargs: None)

    def fail(**kwargs):
        raise OSError("broker down")

    monkeypatch.setattr("apps.campaigns.tasks.execute_run.apply_async", fail)
    with pytest.raises(ExecutionUnavailable):
        start_run(run.pk, idempotency_key="one")
    run.refresh_from_db()
    assert (run.status, run.error_code) == ("failed", "queue_unavailable")
    assert begin_run(run.pk, run.task_id) is None


def test_cancel_before_claim_and_duplicate_claim(run, monkeypatch):
    monkeypatch.setattr("apps.campaigns.services.execution._publish", lambda *_: None)
    run = start_run(run.pk, idempotency_key="one")
    request_cancel(run.pk)
    run.refresh_from_db()
    assert run.status == "cancelled"
    assert begin_run(run.pk, run.task_id) is None
    assert finish_run(run.pk, run.task_id, "completed").status == "cancelled"


def test_running_cancel_is_cooperative_and_terminal_is_immutable(run, monkeypatch):
    monkeypatch.setattr("apps.campaigns.services.execution._publish", lambda *_: None)
    run = start_run(run.pk, idempotency_key="one")
    assert begin_run(run.pk, run.task_id).status == "running"
    assert begin_run(run.pk, run.task_id) is None
    request_cancel(run.pk)
    assert cancellation_requested(run.pk, run.task_id)
    assert finish_run(run.pk, run.task_id, "cancelled").status == "cancelled"
    assert finish_run(run.pk, run.task_id, "failed", "late").status == "cancelled"


class FakeEnvironment:
    def run_pilot(self, **kwargs):
        return {"cost": "40.00", "n_customers": kwargs["n_customers"], "outcome": "observed"}


class FakeRunner:
    def environment(self, context):
        return FakeEnvironment()

    def act(self, env):
        env.run_pilot(n_customers=10, channel="sms")
        return [{"campaign_name": "Test", "target_tariff": "tariff_1", "channel": "sms"}]


def test_pilots_and_real_agent_output_are_saved_incrementally(run):
    CampaignRun.objects.filter(pk=run.pk).update(status="running")
    run_engine(run, check_cancel=lambda: False, runner=FakeRunner())
    pilot = Pilot.objects.get(run=run)
    assert (pilot.sequence, pilot.cost, pilot.n_customers) == (1, Decimal("40.00"), 10)
    assert CampaignResult.objects.get(run=run).campaign["target_tariff"] == "tariff_1"
    assert RunResult.objects.get(run=run).summary == {}
    assert list(RunEvent.objects.values_list("kind", flat=True)) == [
        "pilot_completed", "campaign_result", "result_ready",
    ]


def test_cancel_between_pilots_preserves_only_completed_pilot(run):
    CampaignRun.objects.filter(pk=run.pk).update(status="running")
    class CancellingRunner(FakeRunner):
        def act(self, env):
            env.run_pilot(n_customers=10, channel="sms")
            env.run_pilot(n_customers=10, channel="sms")

    with pytest.raises(ExecutionCancelled):
        run_engine(run, check_cancel=lambda: Pilot.objects.filter(run=run).exists(),
                   runner=CancellingRunner())
    assert Pilot.objects.filter(run=run).count() == 1
    assert not RunResult.objects.filter(run=run).exists()
