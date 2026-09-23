import pytest
from apps.campaigns.models import CampaignRun, Dataset, RunEvent, TeamArtifact, TeamCommand
from apps.campaigns.services.engine_bridge import run_engine
from apps.campaigns.services.execution import ExecutionUnavailable
from apps.campaigns.services.team_state import (
    get_team_snapshot,
    load_snapshot,
    persist_team_event,
    publish_artifact,
)
from campaign_engine.engine_types import EngineResult

pytestmark = pytest.mark.django_db


@pytest.fixture
def run(tmp_path):
    dataset = Dataset.objects.create(name="case", checksum="a" * 64,
                                     source_dir=str(tmp_path), customer_count=10)
    return CampaignRun.objects.create(name="plan", dataset=dataset, status="running")


def _event(run, kind="task_started", **changes):
    return persist_team_event(run_id=run.pk, kind=kind, payload={
        "task_id": "research", "actor_id": "analyst", "status": "running",
        "title": "Review observations", "artifact_ids": [], "evidence_ids": [], **changes,
    })


def test_artifact_published_before_event_and_immutable(run):
    _event(run)
    prior = RunEvent.objects.create(run=run, kind="pilot_completed", payload={"sequence": 1})
    artifact = {"id": "finding-1", "task_id": "research", "type": "validation",
                "title": "Checked result", "data": {"valid": True},
                "evidence_ids": [prior.id]}
    event = _event(run, "task_completed", status="completed", artifacts=[artifact])
    assert event.payload["artifact_ids"] == ["finding-1"]
    saved = TeamArtifact.objects.get(pk="finding-1")
    assert saved.created_at <= event.created_at
    with pytest.raises(ValueError, match="immutable"):
        saved.save()
    assert publish_artifact(run_id=run.pk, artifact=artifact)["id"] == "finding-1"
    with pytest.raises(ValueError, match="immutable"):
        publish_artifact(run_id=run.pk, artifact={**artifact, "title": "changed"})


def test_cross_run_references_rejected_and_rollback(run):
    other = CampaignRun.objects.create(name="other", dataset=run.dataset)
    _event(run)
    artifact = {"id": "one", "task_id": "research", "type": "validation",
                "title": "Evidence", "data": {}, "evidence_ids": []}
    publish_artifact(run_id=run.pk, artifact=artifact)
    with pytest.raises(ValueError, match="another run"):
        publish_artifact(run_id=other.pk, artifact=artifact)
    with pytest.raises(ValueError, match="does not belong"):
        persist_team_event(run_id=other.pk, kind="task_started", payload={
            "task_id": "new", "actor_id": "lead", "title": "Other",
            "artifact_ids": ["one"]})
    assert not other.team_tasks.exists()


def test_snapshot_cursor_and_restoration_do_not_run_engine(run):
    first = _event(run)
    snapshot = get_team_snapshot(run_id=run.pk)
    assert snapshot["last_event_id"] == first.id
    stored = load_snapshot(run_id=run.pk, snapshot_id=snapshot["snapshot_id"])
    assert stored.public_state["tasks"][0]["status"] == "running"
    with pytest.raises(ValueError, match="immutable"):
        stored.save()
    _event(run, "task_completed", status="completed")
    assert stored.public_state["tasks"][0]["status"] == "running"
    assert list(run.events.filter(id__gt=snapshot["last_event_id"]).values_list(
        "kind", flat=True)) == ["task_completed"]
    current = get_team_snapshot(run_id=run.pk)
    assert current["tasks"][0]["status"] == "completed"
    with pytest.raises(load_snapshot.__globals__["TeamSnapshot"].DoesNotExist):
        load_snapshot(run_id=CampaignRun.objects.create(name="other", dataset=run.dataset).pk,
                      snapshot_id=snapshot["snapshot_id"])


def test_cancelled_snapshot_and_command_unique_key(run):
    _event(run)
    run.status = "cancelled"
    run.save(update_fields=["status"])
    snapshot = get_team_snapshot(run_id=run.pk)
    assert snapshot["tasks"][0]["status"] == "cancelled"
    TeamCommand.objects.create(run=run, snapshot_id=snapshot["snapshot_id"], type="explain",
                               idempotency_key="same", request_hash="a" * 64)
    assert run.team_commands.count() == 1
    other = CampaignRun.objects.create(name="other", dataset=run.dataset)
    with pytest.raises(ValueError, match="same run"):
        TeamCommand.objects.create(run=other, snapshot_id=snapshot["snapshot_id"],
                                   type="explain", idempotency_key="same",
                                   request_hash="a" * 64)


def test_handoff_creates_pending_next_task_and_checks_actors(run):
    _event(run)
    event = _event(run, "task_handoff", from_actor="analyst", to_actor="finance",
                   to_task_id="budget", to_title="Check budget")
    assert event.payload["to_task_id"] == "budget"
    assert run.team_tasks.get(task_id="budget").status == "pending"
    with pytest.raises(ValueError, match="actors"):
        _event(run, "task_handoff", from_actor="lead", to_actor="finance",
               to_task_id="invalid")
    assert not run.team_tasks.filter(task_id="invalid").exists()


def test_bridge_observer_saves_task_artifact_and_event(run, monkeypatch):
    import apps.campaigns.services.engine_bridge as bridge

    monkeypatch.setattr(bridge, "load_history", lambda path: None)
    monkeypatch.setattr(bridge, "SegmentIndex", lambda profile: None)

    class Environment:
        customer_profile = None

    monkeypatch.setattr(bridge, "_public_environment", lambda context: Environment())

    def fake_engine(env, config, *, observer, **kwargs):
        observer({"sequence": 1, "type": "task_started", "data": {
            "task_id": "analysis", "actor_id": "analyst", "status": "running",
            "title": "Analyse", "artifact_ids": [], "evidence_ids": []}})
        observer({"sequence": 2, "type": "task_completed", "data": {
            "task_id": "analysis", "actor_id": "analyst", "status": "completed",
            "title": "Analyse", "artifact_ids": [], "evidence_ids": [],
            "artifacts": [{"id": "analysis-result", "task_id": "analysis", "type": "validation",
                           "title": "Checks", "data": {"ok": True}, "evidence_ids": []}]}})
        return EngineResult(status="completed", campaigns=[{
            "campaign_name": "Plan", "target_tariff": "tariff_2", "channel": "push"}],
            estimates={"campaigns": [{"n_contacts": 1, "cost": "0.00"}]})

    monkeypatch.setattr(bridge, "run_campaigns", fake_engine)
    run_engine(run, check_cancel=lambda: False)
    assert run.team_tasks.get(task_id="analysis").artifact_ids == ["analysis-result"]
    assert run.events.get(kind="task_completed").payload["artifact_ids"] == [
        "analysis-result"]
    assert run.team_artifacts.count() == 1


def test_unsupported_constraint_fails_before_engine_or_environment(run, monkeypatch):
    run.constraints = {"allowed_channels": ["sms"]}
    run.save(update_fields=["constraints"])
    monkeypatch.setattr("apps.campaigns.services.engine_bridge._public_environment",
                        lambda context: pytest.fail("environment must not start"))
    with pytest.raises(ExecutionUnavailable, match="allowed_channels"):
        run_engine(run, check_cancel=lambda: False)
