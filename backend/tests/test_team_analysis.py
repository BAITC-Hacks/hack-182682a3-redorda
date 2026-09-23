"""Analysis commands execute the installed engine against an immutable snapshot."""

import pytest
from apps.campaigns.models import (
    CampaignResult,
    CampaignRun,
    Dataset,
    Pilot,
    RunEvent,
    RunResult,
    TeamCommand,
)
from apps.campaigns.services.team_commands import execute_command
from apps.campaigns.services.team_state import get_team_snapshot, load_snapshot

pytestmark = pytest.mark.django_db


def test_real_snapshot_analysis_has_durable_evidence_and_no_paid_work():
    dataset = Dataset.objects.create(name="Analysis", checksum="d" * 64,
                                     source_dir="/unused", customer_count=10)
    run = CampaignRun.objects.create(name="Saved", dataset=dataset, status="completed",
                                     budget=100, max_contacts=20)
    Pilot.objects.create(run=run, sequence=1, request={"channel": "sms"},
                         response={"cost": 20, "n_customers": 2}, cost=20, n_customers=2)
    event = RunEvent.objects.create(run=run, kind="pilot_completed", payload={"sequence": 1})
    CampaignResult.objects.create(run=run, rank=1, campaign={
        "campaign_name": "evolve_1_a_b", "channel": "push", "filter_current_tariff": "a",
        "filter_arpu_segment": "low"}, metrics={
        "cost": 30, "n_contacts": 5, "estimated_incremental_net": 70})
    RunResult.objects.create(run=run, summary={"estimates": {
        "source": "posterior_forecast_not_official_score", "net_arpu_gain_mean": 80}})
    public = get_team_snapshot(run_id=run.pk)
    assert set(public["available_commands"]) == {"create_plan", "explain", "compare"}
    snapshot = load_snapshot(run_id=run.pk, snapshot_id=public["snapshot_id"])
    assert event.id in snapshot.engine_state["evidence_ids"]
    for kind, parameters in [("explain", {"campaign_id": "evolve_1_a_b"}),
                             ("compare", {"constraints": {"budget": "60"}})]:
        command = TeamCommand.objects.create(run=run, snapshot=snapshot, type=kind,
                                             parameters=parameters, idempotency_key=kind,
                                             request_hash="a" * 64)
        execute_command(command.pk)
        command.refresh_from_db()
        assert command.status == "completed", command.error
        assert event.id in command.result["evidence_ids"]
    assert command.result["data"]["alternative"]["total_cost"] == 50
    assert run.pilots.count() == 1
    assert run.events.filter(kind="pilot_completed").count() == 1
    run.refresh_from_db()
    assert run.status == "completed" and run.budget == 100
