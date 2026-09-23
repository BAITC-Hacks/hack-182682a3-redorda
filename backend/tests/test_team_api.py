import sys
from types import ModuleType
from uuid import uuid4

import pytest
from apps.campaigns.models import CampaignRun, Dataset
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def run():
    dataset = Dataset.objects.create(
        name="Test", checksum="b" * 64, source_dir="/unused", customer_count=1
    )
    return CampaignRun.objects.create(name="Plan", dataset=dataset)


@pytest.fixture
def client():
    result = APIClient()
    result.force_authenticate(user=get_user_model().objects.create_user(username="team-test"))
    return result


@pytest.fixture
def services(monkeypatch):
    state = ModuleType("apps.campaigns.services.team_state")
    commands = ModuleType("apps.campaigns.services.team_commands")
    monkeypatch.setitem(sys.modules, state.__name__, state)
    monkeypatch.setitem(sys.modules, commands.__name__, commands)
    state.load_snapshot = lambda **kwargs: object()
    state.get_team_snapshot = lambda *, run_id: {
        "schema_version": 1,
        "run_id": str(run_id),
        "snapshot_id": "snapshot-1",
        "last_event_id": 7,
        "tasks": [],
        "artifacts": [],
        "available_commands": ["explain", "compare", "create_plan"],
        "engine_state": {"secret": "hidden"},
    }
    saved = {}

    def submit_command(*, run_id, command_type, snapshot_id, parameters, idempotency_key):
        previous = saved.get((run_id, idempotency_key))
        signature = (command_type, snapshot_id, parameters)
        if previous:
            if previous[0] != signature:
                raise ValueError("private conflict")
            return previous[1]
        response = {
            "id": "command-1",
            "run_id": str(run_id),
            "type": command_type,
            "status": "completed",
            "result": {"simulator_result": None},
            "error": None,
        }
        saved[(run_id, idempotency_key)] = (signature, response)
        return response

    commands.submit_command = submit_command
    commands.get_command = lambda *, run_id, command_id: {
        "id": command_id,
        "run_id": str(run_id),
        "type": "explain",
        "status": "completed",
        "result": None,
        "error": None,
    }
    return state, commands


def test_snapshot_and_command_roundtrip(client, run, services):
    url = f"/api/v1/runs/{run.id}/team/"
    snapshot = client.get(url)
    assert snapshot.status_code == 200
    assert snapshot.data["last_event_id"] == 7
    assert "engine_state" not in snapshot.data
    command_url = f"/api/v1/runs/{run.id}/commands/"
    payload = {
        "type": "compare",
        "snapshot_id": "snapshot-1",
        "parameters": {"constraints": {"budget": "100.00", "allowed_channels": ["sms"]}},
    }
    first = client.post(command_url, payload, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
    assert first.status_code == 202, first.data
    assert first.data["result"]["simulator_result"] is None
    retry = client.post(command_url, payload, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
    assert retry.data == first.data
    different = {**payload, "parameters": {"constraints": {"budget": "200.00"}}}
    conflict = client.post(command_url, different, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
    assert conflict.status_code == 409
    assert conflict.data["error"]["code"] == "team_conflict"
    detail = client.get(f"{command_url}command-1/")
    assert detail.status_code == 200 and detail.data["id"] == "command-1"


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"type": "explain", "snapshot_id": "s", "parameters": {}}, "parameters"),
        (
            {"type": "explain", "snapshot_id": "s", "parameters": {"campaign_id": "x", "extra": 1}},
            "parameters",
        ),
        ({"type": "compare", "snapshot_id": "s", "parameters": {"constraints": {}}}, "parameters"),
        (
            {"type": "compare", "snapshot_id": "s", "parameters": {"constraints": {"budget": 4}}},
            "parameters",
        ),
        (
            {
                "type": "compare",
                "snapshot_id": "s",
                "parameters": {"constraints": {"allowed_channels": ["unknown"]}},
            },
            "parameters",
        ),
        (
            {
                "type": "create_plan",
                "snapshot_id": "s",
                "parameters": {"name": "", "constraints": {"budget": "5.00"}},
            },
            "parameters",
        ),
        ({"type": "explain", "snapshot_id": "", "parameters": {"campaign_id": "x"}}, "snapshot_id"),
        ({"type": "unknown", "snapshot_id": "s", "parameters": {}}, "type"),
        (
            {"type": "explain", "snapshot_id": "s", "parameters": {"campaign_id": "x"}, "extra": 1},
            "extra",
        ),
    ],
)
def test_invalid_commands(client, run, services, payload, field):
    response = client.post(
        f"/api/v1/runs/{run.id}/commands/", payload, format="json", HTTP_IDEMPOTENCY_KEY="key"
    )
    assert response.status_code == 400
    assert field in response.data["error"]["fields"]


def test_missing_key_foreign_run_and_service_failure(client, run, services):
    command_url = f"/api/v1/runs/{run.id}/commands/"
    payload = {"type": "explain", "snapshot_id": "s", "parameters": {"campaign_id": "x"}}
    assert client.post(command_url, payload, format="json").status_code == 400
    unknown = uuid4()
    assert client.get(f"/api/v1/runs/{unknown}/team/").status_code == 404
    assert client.get(f"/api/v1/runs/{unknown}/commands/c/").status_code == 404
    state, commands = services
    commands.get_command = lambda **kwargs: {"run_id": str(unknown), "id": "c"}
    assert client.get(f"{command_url}c/").status_code == 404

    def broken(**kwargs):
        raise RuntimeError("secret traceback /internal/path")

    state.get_team_snapshot = broken
    failed = client.get(f"/api/v1/runs/{run.id}/team/")
    assert failed.status_code == 503
    assert "secret" not in str(failed.data)


def test_schema_has_team_endpoints(client):
    schema = client.get("/api/schema/?format=json").json()
    for path in (
        "/api/v1/runs/{id}/team/",
        "/api/v1/runs/{id}/commands/",
        "/api/v1/runs/{id}/commands/{command_id}/",
    ):
        assert path in schema["paths"]
    parameters = schema["paths"]["/api/v1/runs/{id}/commands/"]["post"]["parameters"]
    assert any(item["name"] == "Idempotency-Key" and item["required"] for item in parameters)


def test_missing_services_are_explicit(client, run):
    response = client.get(f"/api/v1/runs/{run.id}/team/")
    assert response.status_code == 503
    assert response.data["error"]["code"] == "team_unavailable"


def test_session_authentication_and_csrf(settings, run, services):
    settings.REDORDA_REQUIRE_AUTH = True
    client = APIClient(enforce_csrf_checks=True)
    team_url = f"/api/v1/runs/{run.id}/team/"
    command_url = f"/api/v1/runs/{run.id}/commands/"
    payload = {
        "type": "explain",
        "snapshot_id": "snapshot-1",
        "parameters": {"campaign_id": "campaign-1"},
    }
    assert client.get(team_url).status_code == 403
    assert (
        client.post(command_url, payload, format="json", HTTP_IDEMPOTENCY_KEY="key").status_code
        == 403
    )
    user = get_user_model().objects.create_user(username="session-team", password="password")
    assert client.login(username=user.username, password="password")
    assert client.get(team_url).status_code == 200
    assert (
        client.post(command_url, payload, format="json", HTTP_IDEMPOTENCY_KEY="key").status_code
        == 403
    )
    token = client.get("/api/v1/auth/csrf/").data["csrf_token"]
    assert (
        client.post(
            command_url, payload, format="json", HTTP_X_CSRFTOKEN=token, HTTP_IDEMPOTENCY_KEY="key"
        ).status_code
        == 202
    )
