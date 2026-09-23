"""Command integration against real persistence; AI and broker are test boundaries."""

from types import SimpleNamespace

import pytest
from apps.campaigns.models import (
    CampaignRun,
    Dataset,
    TeamArtifact,
    TeamCommand,
    TeamSnapshot,
    TeamTask,
)
from apps.campaigns.services import team_ai, team_commands
from rest_framework.exceptions import ValidationError


@pytest.mark.parametrize("command_type,parameters", [
    ("explain", {"campaign_id": ""}),
    ("explain", {"campaign_id": "one", "unknown": True}),
    ("compare", {"constraints": {"budget": 10}}),
    ("compare", {"constraints": {"budget": "NaN"}}),
    ("compare", {"constraints": {"budget": "0"}}),
    ("compare", {"constraints": {"allowed_channels": ["sms", "sms"]}}),
    ("compare", {"constraints": {"allowed_channels": ["invented"]}}),
    ("create_plan", {"name": " ", "constraints": {"budget": "100"}}),
])
def test_invalid_parameters_are_rejected(command_type, parameters):
    with pytest.raises(ValidationError):
        team_commands._parameters(command_type, parameters)


def test_ai_adapter_reports_missing_capability(monkeypatch):
    monkeypatch.setattr(team_ai, "import_module", lambda _: SimpleNamespace())
    with pytest.raises(team_ai.CapabilityUnavailable):
        team_ai.explain({}, "campaign-1")
    assert team_ai.available_command_types({"campaigns": [{"id": 1}]}) == ["create_plan"]


def test_ai_adapter_calls_real_function_if_present(monkeypatch):
    seen = []
    module = SimpleNamespace(explain=lambda state, campaign: seen.append((state, campaign)) or
                             {"type": "explanation", "evidence_ids": []})
    monkeypatch.setattr(team_ai, "import_module", lambda _: module)
    result = team_ai.explain({"pilots": []}, "campaign-1")
    assert seen == [({"pilots": []}, "campaign-1")]
    assert result["type"] == "explanation"
    assert team_ai.available_command_types({"campaigns": [{"id": 1}]}) == [
        "create_plan", "explain"]
    assert team_ai.available_command_types({"campaigns": []}) == ["create_plan"]


@pytest.fixture
def saved_run(db):
    dataset = Dataset.objects.create(name="Commands", checksum="c" * 64,
                                     source_dir="/unused", customer_count=10)
    run = CampaignRun.objects.create(name="Original", dataset=dataset, status="completed")
    state = {"schema_version": 1, "pilots": [{"sequence": 1}],
             "campaigns": [{"id": "campaign-1"}]}
    snapshot = TeamSnapshot.objects.create(run=run, engine_state=state)
    return run, snapshot


def _command(saved_run, command_type="explain", parameters=None):
    run, snapshot = saved_run
    return TeamCommand.objects.create(
        run=run, snapshot=snapshot, type=command_type,
        parameters=parameters or {"campaign_id": "campaign-1"},
        idempotency_key="key", request_hash="a" * 64,
    )


def _artifact(kind="explanation"):
    return {"id": "ai-result", "task_id": "ai-does-not-own-task-identity", "type": kind,
            "title": "Facts from saved observations", "data": {"forecast": None},
            "evidence_ids": []}


def test_idempotency_normalizes_body_and_records_hash(saved_run, monkeypatch,
                                                    django_capture_on_commit_callbacks):
    run, snapshot = saved_run
    published = []
    monkeypatch.setattr(team_commands, "_publish", published.append)
    args = {"run_id": run.pk, "command_type": "create_plan",
            "snapshot_id": str(snapshot.pk).upper(), "parameters": {
                "name": " New ", "constraints": {"budget": "100",
                                                      "allowed_channels": ["sms", "push"]}},
            "idempotency_key": "key"}
    with django_capture_on_commit_callbacks(execute=True):
        first = team_commands.submit_command(**args)
        repeated = team_commands.submit_command(**{**args, "snapshot_id": str(snapshot.pk),
            "parameters": {"name": "New", "constraints": {"budget": "100.00",
                                                             "allowed_channels": ["push", "sms"]}}})
    assert first == repeated
    assert TeamCommand.objects.count() == len(published) == 1
    command = TeamCommand.objects.get()
    assert len(command.request_hash) == 64 and command.parameters["name"] == "New"
    assert command.parameters["constraints"]["budget"] == "100.00"
    with pytest.raises(team_commands.CommandConflict):
        team_commands.submit_command(**{**args, "parameters": {
            "name": "Changed", "constraints": {"budget": "100"}}})


def test_queue_failure_is_terminal_and_late_delivery_ignored(saved_run, monkeypatch):
    from apps.campaigns import tasks

    command = _command(saved_run)
    monkeypatch.setattr(tasks.expire_team_command, "apply_async", lambda **_: None)
    monkeypatch.setattr(tasks.execute_team_command, "apply_async",
                        lambda **_: (_ for _ in ()).throw(OSError("Redis unavailable")))
    team_commands._publish(command.pk)
    command.refresh_from_db()
    assert command.status == "failed" and command.error["code"] == "queue_unavailable"
    assert team_commands._claim(command.pk) is None
    assert TeamTask.objects.get().status == "failed"


def test_explain_uses_saved_state_once_and_saves_real_task_artifact(saved_run, monkeypatch):
    run, snapshot = saved_run
    command = _command(saved_run)
    calls = []
    monkeypatch.setattr(team_ai, "explain", lambda state, campaign: calls.append(
        (state, campaign)) or _artifact())
    team_commands.execute_command(command.pk)
    team_commands.execute_command(command.pk)
    command.refresh_from_db()
    assert calls == [(snapshot.engine_state, "campaign-1")]
    assert command.status == "completed" and command.error is None
    artifact = TeamArtifact.objects.get()
    task = TeamTask.objects.get()
    assert artifact.task_id == task.pk and task.task_id == f"command:{command.pk}"
    assert task.status == "completed" and artifact.id in task.artifact_ids
    assert command.result["id"] == artifact.id
    assert run.pilots.count() == 0
    run.refresh_from_db()
    assert run.status == "completed"


def test_late_ai_result_after_timeout_cannot_publish_artifact(saved_run, monkeypatch):
    command = _command(saved_run)

    def expire_during_ai(*_):
        team_commands.expire_command(command.pk)
        return _artifact()

    monkeypatch.setattr(team_ai, "explain", expire_during_ai)
    team_commands.execute_command(command.pk)
    command.refresh_from_db()
    assert command.status == "failed" and command.error["code"] == "timeout"
    assert command.result is None and not TeamArtifact.objects.exists()
    assert TeamTask.objects.get().status == "failed"


def test_artifact_and_completion_rollback_together(saved_run, monkeypatch):
    from apps.campaigns.services import team_state

    command = _command(saved_run)
    original = team_state.persist_team_event

    def fail_completion(**kwargs):
        if kwargs["kind"] == "task_completed":
            raise RuntimeError("event write failed")
        return original(**kwargs)

    monkeypatch.setattr(team_state, "persist_team_event", fail_completion)
    monkeypatch.setattr(team_ai, "explain", lambda *_: _artifact())
    team_commands.execute_command(command.pk)
    command.refresh_from_db()
    assert command.status == "failed" and command.error["code"] == "command_failed"
    assert command.result is None and not TeamArtifact.objects.exists()
    assert TeamTask.objects.get().status == "failed"


def test_ai_failure_is_persisted_without_retry(saved_run, monkeypatch):
    command = _command(saved_run, "compare", {"constraints": {"budget": "100.00"}})
    calls = []

    def fail(*_):
        calls.append(1)
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(team_ai, "compare", fail)
    team_commands.execute_command(command.pk)
    team_commands.execute_command(command.pk)
    command.refresh_from_db()
    assert calls == [1]
    assert command.status == "failed" and command.error["code"] == "command_failed"
    assert command.result is None


def test_create_plan_clones_settings_without_pilots(saved_run):
    run, _ = saved_run
    command = _command(saved_run, "create_plan", {
        "name": "New", "constraints": {"budget": "250.00"}})
    team_commands.execute_command(command.pk)
    command.refresh_from_db()
    plan = CampaignRun.objects.get(pk=command.result["run_id"])
    assert command.status == "completed" and plan.status == "draft"
    assert plan.parent_run_id == run.pk and plan.dataset_id == run.dataset_id
    assert plan.seed == run.seed and plan.strategy == run.strategy
    assert plan.budget == 250 and plan.constraints == {"budget": "250.00"}
    assert plan.pilots.count() == 0
    assert TeamTask.objects.get().status == "completed"


def test_unsupported_channel_restriction_does_not_create_plan(saved_run, monkeypatch):
    monkeypatch.setattr(team_commands, "RunConfig", SimpleNamespace(model_fields={}))
    command = _command(saved_run, "create_plan", {
        "name": "New", "constraints": {"allowed_channels": ["push"]}})
    team_commands.execute_command(command.pk)
    command.refresh_from_db()
    assert command.status == "failed" and command.error["code"] == "capability_unavailable"
    assert CampaignRun.objects.count() == 1
