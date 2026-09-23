import os
from decimal import Decimal
from pathlib import Path

import pytest
from apps.campaigns.services.engine_bridge import EngineContext
from apps.campaigns.services.execution import ExecutionUnavailable
from django.utils.module_loading import import_string

FACTORY = "apps.campaigns.services.public_environment.local_simulation"


def test_built_in_factory_rejects_unverified_code(tmp_path, settings):
    settings.PARTICIPANT_KIT_DIR = tmp_path
    (tmp_path / "mock_environment.py").write_text("raise AssertionError('must not execute')")
    factory = import_string(FACTORY)
    with pytest.raises(ExecutionUnavailable):
        factory(EngineContext(tmp_path, Decimal(100), 35, 1, 19, "baseline"))


@pytest.mark.skipif(not os.getenv("REDORDA_OFFICIAL_KIT_TEST"), reason="Official kit opt-in")
def test_official_local_simulator_uses_explicit_profile_and_seed(settings):
    kit = Path(os.environ["REDORDA_OFFICIAL_KIT_TEST"]).resolve()
    settings.PARTICIPANT_KIT_DIR = kit
    context = EngineContext(kit, Decimal(100000), 15000, 1, 19, "baseline")
    factory = import_string(FACTORY)
    first, second = factory(context), factory(context)
    assert len(first.customer_profile) == 23441
    assert first.pilot_history == second.pilot_history == []
    request = {"target_tariff": "tariff_4", "channel": "sms", "n_customers": 10,
               "filter_current_tariff": "tariff_1", "filter_arpu_segment": "HIGH"}
    assert first.run_pilot(**request) == second.run_pilot(**request)
    assert first.remaining_budget == 99960


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(not os.getenv("REDORDA_OFFICIAL_KIT_TEST"), reason="Official kit opt-in")
def test_official_dataset_import_to_real_runner_and_export(settings, monkeypatch):
    from apps.campaigns.models import CampaignRun
    from apps.campaigns.services import execution
    from apps.campaigns.tasks import execute_run
    from django.core.management import call_command
    from rest_framework.test import APIClient

    kit = Path(os.environ["REDORDA_OFFICIAL_KIT_TEST"]).resolve()
    settings.PARTICIPANT_KIT_DIR = kit
    settings.REDORDA_RUN_EXECUTION_ENABLED = True
    settings.REDORDA_ENVIRONMENT_FACTORY = FACTORY
    call_command("import_participant_data", path=kit)
    monkeypatch.setattr(execution, "_publish", lambda run_id, task_id:
                        execute_run.apply(args=[str(run_id)], task_id=task_id, throw=True))
    client = APIClient()
    assert client.get("/api/v1/meta/").json()["environment"]["mode"] == "local_simulation"
    created = client.post("/api/v1/runs/", {"name": "Official local simulation",
                          "budget": "1000.00", "max_contacts": 5000,
                          "max_pilots": 1, "seed": 19}, format="json")
    assert created.status_code == 201
    url = f"/api/v1/runs/{created.json()['id']}/"
    assert client.post(url + "start/", HTTP_IDEMPOTENCY_KEY="official").status_code == 202
    run = CampaignRun.objects.get(pk=created.json()["id"])
    assert run.status == "completed", run.error_message
    result = client.get(url + "results/").json()
    assert Decimal(result["totals"]["total_cost"]) <= 1000
    assert result["totals"]["total_contacts"] <= 5000
    assert result["totals"]["predicted_effect"] is not None
    assert result["totals"]["simulator_result"] is None
    assert run.pilots.count() == 1
    team = client.get(url + 'team/').json()
    assert {task['actor_id'] for task in team['tasks']} == {
        'lead', 'analyst', 'experiment', 'finance', 'control'}
    assert all(task['status'] == 'completed' for task in team['tasks'])
    saved_ids = {artifact['id'] for artifact in team['artifacts']}
    assert saved_ids
    for task in team['tasks']:
        assert task['artifact_ids']
        assert set(task['artifact_ids'] + task['evidence_ids']) <= saved_ids
    assert run.events.filter(kind='task_handoff').exists()
    restored = client.get(url + 'team/').json()
    assert restored['tasks'] == team['tasks']
    assert restored['artifacts'] == team['artifacts']
    assert restored['last_event_id'] == team['last_event_id']
    export = client.get(url + "export/", HTTP_ACCEPT="text/csv, application/json")
    assert export.status_code == 200
    assert b"tariff_" in b"".join(export.streaming_content)
