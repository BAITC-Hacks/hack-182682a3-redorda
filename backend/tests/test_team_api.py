import sys
from types import ModuleType
from uuid import uuid4

import pytest
from apps.campaigns.models import CampaignRun, Dataset
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

SNAPSHOT_ID = "42ef7f84-a9f4-4775-a783-e1e6d244ac55"
COMMAND_ID = "9749591a-fc5c-4d0d-94fb-330e7b864958"


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
        "snapshot_id": SNAPSHOT_ID,
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
            "id": COMMAND_ID,
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
        "snapshot_id": SNAPSHOT_ID,
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
    detail = client.get(f"{command_url}{COMMAND_ID}/")
    assert detail.status_code == 200 and detail.data["id"] == COMMAND_ID


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"type": "explain", "snapshot_id": SNAPSHOT_ID, "parameters": {}}, "parameters"),
        (
            {
                "type": "explain",
                "snapshot_id": SNAPSHOT_ID,
                "parameters": {"campaign_id": "x", "extra": 1},
            },
            "parameters",
        ),
        (
            {"type": "compare", "snapshot_id": SNAPSHOT_ID, "parameters": {"constraints": {}}},
            "parameters",
        ),
        (
            {
                "type": "compare",
                "snapshot_id": SNAPSHOT_ID,
                "parameters": {"constraints": {"budget": 4}},
            },
            "parameters",
        ),
        (
            {
                "type": "compare",
                "snapshot_id": SNAPSHOT_ID,
                "parameters": {"constraints": {"allowed_channels": ["unknown"]}},
            },
            "parameters",
        ),
        (
            {
                "type": "create_plan",
                "snapshot_id": SNAPSHOT_ID,
                "parameters": {"name": "", "constraints": {"budget": "5.00"}},
            },
            "parameters",
        ),
        ({"type": "explain", "snapshot_id": "", "parameters": {"campaign_id": "x"}}, "snapshot_id"),
        ({"type": "unknown", "snapshot_id": SNAPSHOT_ID, "parameters": {}}, "type"),
        (
            {
                "type": "explain",
                "snapshot_id": SNAPSHOT_ID,
                "parameters": {"campaign_id": "x"},
                "extra": 1,
            },
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
    payload = {"type": "explain", "snapshot_id": SNAPSHOT_ID, "parameters": {"campaign_id": "x"}}
    assert client.post(command_url, payload, format="json").status_code == 400
    unknown = uuid4()
    assert client.get(f"/api/v1/runs/{unknown}/team/").status_code == 404
    assert client.get(f"/api/v1/runs/{unknown}/commands/c/").status_code == 404
    state, commands = services
    commands.get_command = lambda **kwargs: {"run_id": str(unknown), "id": "c"}
    assert client.get(f"{command_url}{COMMAND_ID}/").status_code == 404

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


def test_missing_services_are_explicit(client, run, monkeypatch):
    from apps.campaigns import team_views

    def unavailable(_):
        raise ModuleNotFoundError("missing test service")

    monkeypatch.setattr(team_views, "import_module", unavailable)
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
        "snapshot_id": SNAPSHOT_ID,
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


def test_real_create_plan_roundtrip_is_idempotent(
    client, run, monkeypatch, django_capture_on_commit_callbacks
):
    from apps.campaigns.models import TeamCommand
    from apps.campaigns.services import team_commands

    published = []
    monkeypatch.setattr(team_commands, "_publish", published.append)
    snapshot = client.get(f"/api/v1/runs/{run.id}/team/")
    assert snapshot.status_code == 200, snapshot.data
    assert "engine_state" not in snapshot.data
    url = f"/api/v1/runs/{run.id}/commands/"
    payload = {
        "type": "create_plan",
        "snapshot_id": snapshot.data["snapshot_id"],
        "parameters": {
            "name": "Derived plan",
            "constraints": {"budget": "500.00"},
        },
    }
    with django_capture_on_commit_callbacks(execute=True):
        first = client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="real-plan")
    assert first.status_code == 202, first.data
    assert first.data["status"] == "queued"
    command_id = first.data["id"]
    assert [str(item) for item in published] == [command_id]
    retry = client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="real-plan")
    assert retry.status_code == 202 and retry.data == first.data
    assert TeamCommand.objects.filter(run=run).count() == 1

    changed = {
        **payload,
        "parameters": {
            **payload["parameters"],
            "constraints": {"budget": "600.00"},
        },
    }
    conflict = client.post(url, changed, format="json", HTTP_IDEMPOTENCY_KEY="real-plan")
    assert conflict.status_code == 409, conflict.data

    team_commands.execute_command(command_id)
    result = client.get(f"{url}{command_id}/")
    assert result.status_code == 200, result.data
    assert result.data["status"] == "completed", result.data
    derived = CampaignRun.objects.get(pk=result.data["result"]["run_id"])
    assert derived.parent_run_id == run.id
    assert derived.status == "draft"
    assert str(derived.budget) == "500.00"
    assert derived.constraints == payload["parameters"]["constraints"]
    assert (derived.dataset_id, derived.seed, derived.strategy) == (
        run.dataset_id,
        run.seed,
        run.strategy,
    )
    assert not derived.pilots.exists()
    assert not derived.events.exists()
    run.refresh_from_db()
    assert run.status == "draft" and str(run.budget) == "100000.00"
    team_commands.execute_command(command_id)
    assert CampaignRun.objects.filter(parent_run=run).count() == 1


def test_real_missing_and_foreign_snapshot_and_command_return_404(client, run):
    from apps.campaigns.models import TeamCommand, TeamSnapshot

    other = CampaignRun.objects.create(name="Other plan", dataset=run.dataset)
    snapshot = TeamSnapshot.objects.create(run=other)
    foreign_command = TeamCommand.objects.create(
        run=other,
        snapshot=snapshot,
        type="create_plan",
        idempotency_key="foreign",
        request_hash="a" * 64,
    )
    url = f"/api/v1/runs/{run.id}/commands/"
    for snapshot_id in (str(snapshot.pk), str(uuid4())):
        response = client.post(
            url,
            {
                "type": "create_plan",
                "snapshot_id": snapshot_id,
                "parameters": {"name": "New plan", "constraints": {"budget": "10.00"}},
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="missing-snapshot",
        )
        assert response.status_code == 404, response.data
        assert response.data["error"]["code"] == "not_found"
    for command_id in (str(foreign_command.pk), str(uuid4())):
        response = client.get(f"{url}{command_id}/")
        assert response.status_code == 404, response.data
        assert response.data["error"]["code"] == "not_found"
    assert not TeamCommand.objects.filter(run=run).exists()


def test_malformed_snapshot_and_command_ids_return_400(client, run):
    url = f"/api/v1/runs/{run.id}/commands/"
    response = client.post(
        url,
        {
            "type": "explain",
            "snapshot_id": "not-a-uuid",
            "parameters": {"campaign_id": "1"},
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="malformed",
    )
    assert response.status_code == 400, response.data
    assert "snapshot_id" in response.data["error"]["fields"]
    response = client.get(f"{url}not-a-uuid/")
    assert response.status_code == 400, response.data
    assert "command_id" in response.data["error"]["fields"]
