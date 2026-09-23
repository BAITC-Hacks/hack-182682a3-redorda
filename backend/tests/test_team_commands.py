"""Command contract tests; the absent persistence branch is replaced only here."""

import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
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


def test_ai_adapter_reports_missing_capability():
    with pytest.raises(team_ai.CapabilityUnavailable):
        team_ai.explain({}, "campaign-1")


def test_ai_adapter_calls_real_function_if_present(monkeypatch):
    seen = []
    module = SimpleNamespace(explain=lambda state, campaign: seen.append((state, campaign)) or
                             {"type": "explanation", "evidence_ids": ["event-1"]})
    monkeypatch.setattr(team_ai, "import_module", lambda _: module)
    result = team_ai.explain({"pilots": []}, "campaign-1")
    assert seen == [({"pilots": []}, "campaign-1")]
    assert result["evidence_ids"] == ["event-1"]


class _Query:
    def __init__(self, manager, filters):
        self.manager, self.filters = manager, filters

    def first(self):
        return next((item for item in self.manager.items if all(
            getattr(item, key) == value for key, value in self.filters.items())), None)


class _Manager:
    def __init__(self, items=()):
        self.items = list(items)

    def select_for_update(self):
        return self

    def get(self, **kwargs):
        return next(item for item in self.items if all(
            getattr(item, key) == value for key, value in kwargs.items()))

    def filter(self, **kwargs):
        return _Query(self, kwargs)

    def create(self, **kwargs):
        item = _Command(**kwargs)
        self.items.append(item)
        return item


class _Command:
    _meta = SimpleNamespace(fields=[SimpleNamespace(name="command_type")])

    def __init__(self, **values):
        self.__dict__.update(values)
        self.pk = uuid4()
        self.run_id = self.run.pk
        self.snapshot_id = str(self.snapshot_id)

    def refresh_from_db(self):
        pass

    def save(self, **_):
        pass


def test_idempotency_reuses_saved_command_and_rejects_changed_body(monkeypatch):
    run = SimpleNamespace(pk=uuid4())
    manager = _Manager()
    _Command.objects = manager
    snapshot = SimpleNamespace(pk="snapshot-1", schema_version=1, engine_state={})
    state = ModuleType("apps.campaigns.services.team_state")
    state.load_snapshot = lambda **_: snapshot
    monkeypatch.setitem(sys.modules, state.__name__, state)
    monkeypatch.setattr(team_commands, "_command_model", lambda: _Command)
    monkeypatch.setattr(team_commands.CampaignRun, "objects", _Manager([run]))
    monkeypatch.setattr(team_commands.transaction, "atomic", nullcontext)
    monkeypatch.setattr(team_commands.transaction, "on_commit", lambda callback: callback())
    published = []
    monkeypatch.setattr(team_commands, "_publish", published.append)
    args = {"run_id": run.pk, "command_type": "explain", "snapshot_id": "snapshot-1",
            "parameters": {"campaign_id": "campaign-1"}, "idempotency_key": "key"}
    first = team_commands.submit_command(**args)
    assert team_commands.submit_command(**args) == first
    assert len(manager.items) == len(published) == 1
    with pytest.raises(team_commands.CommandConflict):
        team_commands.submit_command(**{**args, "parameters": {"campaign_id": "campaign-2"}})


def test_queue_failure_is_terminal_and_late_worker_delivery_is_ignored(monkeypatch):
    run = SimpleNamespace(pk=uuid4())
    command = _Command(run=run, snapshot_id="snapshot-1", command_type="compare",
                       parameters={}, idempotency_key="key", status="queued",
                       result=None, error=None)
    manager = _Manager([command])
    _Command.objects = manager
    monkeypatch.setattr(team_commands, "_command_model", lambda: _Command)
    monkeypatch.setattr(team_commands.transaction, "atomic", nullcontext)
    from apps.campaigns import tasks
    monkeypatch.setattr(tasks.expire_team_command, "apply_async", lambda **_: None)
    monkeypatch.setattr(tasks.execute_team_command, "apply_async",
                        lambda **_: (_ for _ in ()).throw(OSError("Redis unavailable")))
    team_commands._publish(command.pk)
    assert command.status == "failed" and command.error["code"] == "queue_unavailable"
    assert team_commands._claim(command.pk) is None


def test_explain_uses_saved_state_once_without_running_pilots(monkeypatch):
    run = SimpleNamespace(pk=uuid4(), status="completed", pilots=[])
    command = _Command(run=run, snapshot_id="snapshot-1", command_type="explain",
                       parameters={"campaign_id": "campaign-1"}, idempotency_key="key",
                       status="queued", result=None, error=None)
    _Command.objects = _Manager([command])
    monkeypatch.setattr(team_commands, "_command_model", lambda: _Command)
    monkeypatch.setattr(team_commands.transaction, "atomic", nullcontext)
    state = ModuleType("apps.campaigns.services.team_state")
    saved_input = {"observations": [{"id": "pilot-1"}]}
    state.load_snapshot = lambda **_: SimpleNamespace(engine_state=saved_input)
    saved = []
    state.publish_artifact = lambda **kwargs: saved.append(kwargs) or {"id": "artifact-1"}
    monkeypatch.setitem(sys.modules, state.__name__, state)
    calls = []
    monkeypatch.setattr(team_ai, "explain", lambda snapshot, campaign_id: calls.append(
        (snapshot, campaign_id)) or {"type": "explanation", "evidence_ids": ["pilot-1"]})
    team_commands.execute_command(command.pk)
    team_commands.execute_command(command.pk)
    assert calls == [(saved_input, "campaign-1")]
    assert saved[0]["run_id"] == run.pk
    assert command.status == "completed" and command.result == {"id": "artifact-1"}
    assert run.status == "completed" and run.pilots == []


def test_ai_failure_is_persisted_without_retry(monkeypatch):
    run = SimpleNamespace(pk=uuid4(), status="completed", pilots=[])
    command = _Command(run=run, snapshot_id="snapshot-1", command_type="compare",
                       parameters={"constraints": {"budget": "100.00"}},
                       idempotency_key="key", status="queued", result=None, error=None)
    _Command.objects = _Manager([command])
    monkeypatch.setattr(team_commands, "_command_model", lambda: _Command)
    monkeypatch.setattr(team_commands.transaction, "atomic", nullcontext)
    state = ModuleType("apps.campaigns.services.team_state")
    state.load_snapshot = lambda **_: SimpleNamespace(engine_state={"saved": True})
    monkeypatch.setitem(sys.modules, state.__name__, state)
    attempts = []

    def fail(*_):
        attempts.append(1)
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(team_ai, "compare", fail)
    team_commands.execute_command(command.pk)
    team_commands.execute_command(command.pk)
    assert attempts == [1]
    assert command.status == "failed" and command.error["code"] == "command_failed"
    assert command.result is None and run.status == "completed" and run.pilots == []


def test_create_plan_clones_settings_without_pilots(monkeypatch):
    run = SimpleNamespace(pk=uuid4(), dataset="dataset", budget=100_000,
                          max_contacts=15000, max_pilots=20, seed=42, strategy="baseline",
                          constraints={"legacy_limit": "kept"},
                          pilots=["original observation"])
    command = _Command(run=run, snapshot_id="snapshot-1", command_type="create_plan",
                       parameters={"name": "New", "constraints": {"budget": "250.00",
                                  "allowed_channels": ["sms"]}}, idempotency_key="key",
                       status="running", result=None, error=None)
    _Command.objects = _Manager([command])
    monkeypatch.setattr(team_commands, "_command_model", lambda: _Command)
    monkeypatch.setattr(team_commands.transaction, "atomic", nullcontext)
    created = []
    plan_model = SimpleNamespace(_meta=SimpleNamespace(fields=[
        SimpleNamespace(name="parent_run"), SimpleNamespace(name="constraints")]),
        objects=SimpleNamespace(create=lambda **kwargs: created.append(kwargs) or
                                SimpleNamespace(pk=uuid4())))
    monkeypatch.setattr(team_commands, "CampaignRun", plan_model)
    result = team_commands._create_plan(command)
    assert result == command.result
    assert command.status == "completed"
    assert len(created) == 1
    assert created[0]["parent_run"] is run
    assert created[0]["dataset"] == "dataset"
    assert created[0]["seed"] == 42 and created[0]["strategy"] == "baseline"
    assert created[0]["constraints"] == {"legacy_limit": "kept", "budget": "250.00",
                                          "allowed_channels": ["sms"]}
    assert "pilots" not in created[0]
    assert run.budget == 100_000 and run.pilots == ["original observation"]
