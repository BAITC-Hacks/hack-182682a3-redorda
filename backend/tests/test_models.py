from decimal import Decimal

import pytest
from apps.campaigns.models import CampaignResult, CampaignRun, Dataset, Pilot, RunEvent, RunResult
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db


@pytest.fixture
def runs():
    dataset = Dataset.objects.create(name="Test", checksum="a" * 64, source_dir="/unused",
                                     customer_count=1, summary={})
    return (CampaignRun.objects.create(name="First", dataset=dataset),
            CampaignRun.objects.create(name="Second", dataset=dataset))


def test_run_lifecycle_and_related_records(runs):
    first, second = runs
    assert first.status == CampaignRun.Status.DRAFT
    assert not first.cancel_requested
    first.task_id = "celery-task"
    first.status = CampaignRun.Status.RUNNING
    first.save()
    assert CampaignRun.objects.get(pk=first.pk).task_id == "celery-task"
    event = RunEvent.objects.create(run=first, kind="pilot_started", payload={"sequence": 1})
    assert event.id > 0
    assert list(first.events.values_list("id", flat=True)) == [event.id]
    Pilot.objects.create(run=first, sequence=1, request={"campaign_name": "A"},
                         response={"observed": True}, cost=Decimal("12.34"), n_customers=10)
    Pilot.objects.create(run=second, sequence=1, request={}, response={},
                         cost=Decimal("0.00"), n_customers=1)
    CampaignResult.objects.create(run=first, rank=1, campaign={"campaign_name": "A"},
                                  metrics={"gain": "3.21"}, explanation="Observed")
    CampaignResult.objects.create(run=second, rank=1, campaign={}, metrics={})
    RunResult.objects.create(run=first, summary={"cost": "12.34"})
    assert first.pilots.get().cost == Decimal("12.34")
    assert first.result.summary == {"cost": "12.34"}
    assert first.campaign_results.get().rank == 1


@pytest.mark.parametrize("model,fields", [
    (Pilot, {"sequence": 1, "request": {}, "response": {}, "cost": Decimal("0"),
             "n_customers": 1}),
    (CampaignResult, {"rank": 1, "campaign": {}, "metrics": {}}),
    (RunResult, {"summary": {}}),
])
def test_unique_result_keys_per_run(runs, model, fields):
    first, _ = runs
    model.objects.create(run=first, **fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(run=first, **fields)


@pytest.mark.django_db(transaction=True)
def test_migration_preserves_existing_dataset_and_run():
    executor = MigrationExecutor(connection)
    old = [("campaigns", "0001_initial")]
    new = [("campaigns", "0002_campaignrun_cancel_requested_campaignrun_error_code_and_more")]
    try:
        executor.migrate(old)
        state = executor.loader.project_state(old)
        OldDataset = state.apps.get_model("campaigns", "Dataset")
        OldRun = state.apps.get_model("campaigns", "CampaignRun")
        dataset = OldDataset.objects.create(name="Before", checksum="b" * 64,
                                            source_dir="/old", customer_count=3,
                                            summary={"before": True})
        run = OldRun.objects.create(name="Existing", dataset_id=dataset.pk,
                                    budget=Decimal("10.50"))
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        state = executor.loader.project_state(new)
        NewDataset = state.apps.get_model("campaigns", "Dataset")
        NewRun = state.apps.get_model("campaigns", "CampaignRun")
        assert NewDataset.objects.get(pk=dataset.pk).summary == {"before": True}
        upgraded = NewRun.objects.get(pk=run.pk)
        assert upgraded.budget == Decimal("10.50")
        assert upgraded.status == "draft"
        assert upgraded.cancel_requested is False
        assert upgraded.task_id == ""
    finally:
        MigrationExecutor(connection).migrate(new)
