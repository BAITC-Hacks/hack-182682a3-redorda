import io
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from apps.campaigns.models import CampaignRun, Dataset, Pilot, RunEvent
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


@pytest.fixture
def agent_check(tmp_path, settings, monkeypatch):
    dataset = Dataset.objects.create(
        name="preflight fixture",
        checksum="c" * 64,
        source_dir=str(tmp_path),
        customer_count=12,
    )
    environment = SimpleNamespace(
        customer_profile=pd.DataFrame(
            {
                "ID_NUMBER": [f"private-customer-{number}" for number in range(12)],
                "predicted_arpu": [1000.0] * 12,
                "current_tariff": ["tariff_1"] * 12,
                "arpu_segment": ["MID"] * 12,
                "data_segment": ["LITE"] * 12,
                "call_segment": ["MEDIUM"] * 12,
            }
        ),
        tariffs=pd.DataFrame({"tariff_plan_code": ["tariff_1", "tariff_2"]}),
        channels={
            name: {"cost_per_contact": cost, "conversion_multiplier": multiplier}
            for name, cost, multiplier in (
                ("push", 0, 0.5),
                ("sms", 4, 0.65),
                ("digital_ads", 22, 0.85),
                ("call", 160, 1.2),
            )
        },
        remaining_budget=100000,
        remaining_contacts=15000,
        pilots_left=20,
        pilot_history=[],
        run_pilot=Mock(side_effect=AssertionError("Must not conduct a pilot")),
    )
    factory = Mock(return_value=environment)
    module = ModuleType("readiness_fixture_factory")
    module.create_environment = factory
    monkeypatch.setitem(sys.modules, module.__name__, module)
    settings.REDORDA_ENVIRONMENT_FACTORY = "readiness_fixture_factory.create_environment"
    settings.REDORDA_RUN_EXECUTION_ENABLED = False
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-sol")
    monkeypatch.setenv("OPENAI_API_KEY", "private-key-marker")
    return dataset, environment, factory


def invoke_check():
    stdout, stderr = io.StringIO(), io.StringIO()
    call_command("check_agent", stdout=stdout, stderr=stderr)
    return stdout.getvalue(), stderr.getvalue()


def test_preflight_constructs_fresh_environment_without_actions_or_secret_output(agent_check):
    dataset, environment, factory = agent_check
    stdout, stderr = invoke_check()
    context = factory.call_args.args[0]
    assert str(context.dataset_path) == dataset.source_dir
    assert context.budget == 100000 and context.max_contacts == 15000
    assert context.max_pilots == 20 and context.seed == 42 and context.strategy == "baseline"
    assert "Agent preflight: OK" in stdout
    assert "Execution enabled: false" in stdout
    assert "OpenAI key configured: true; model: gpt-6-sol" in stdout
    assert "private-key-marker" not in stdout + stderr
    assert "private-customer" not in stdout + stderr
    assert dataset.source_dir not in stdout + stderr
    environment.run_pilot.assert_not_called()
    assert not CampaignRun.objects.exists()
    assert not Pilot.objects.exists()
    assert not RunEvent.objects.exists()
    assert Dataset.objects.count() == 1


def test_preflight_checks_latest_dataset_path_instead_of_environment_variable(
    agent_check,
    tmp_path,
    monkeypatch,
):
    _, _, factory = agent_check
    latest_path = tmp_path / "latest"
    latest_path.mkdir()
    Dataset.objects.create(
        name="latest", checksum="d" * 64, source_dir=str(latest_path), customer_count=12
    )
    monkeypatch.setenv("PARTICIPANT_KIT_DIR", "/unrelated/not-selected")
    invoke_check()
    assert factory.call_args.args[0].dataset_path == latest_path


def test_preflight_requires_imported_dataset_without_constructing_environment(agent_check):
    _, _, factory = agent_check
    Dataset.objects.all().delete()
    with pytest.raises(CommandError, match="Import a dataset"):
        invoke_check()
    factory.assert_not_called()


@pytest.mark.parametrize("source_dir", ["relative/path", "/missing/preflight-private-path"])
def test_preflight_rejects_unavailable_dataset_path_without_disclosing_it(agent_check, source_dir):
    dataset, _, factory = agent_check
    dataset.source_dir = source_dir
    dataset.save(update_fields=["source_dir"])
    with pytest.raises(CommandError, match="directory is unavailable") as failure:
        invoke_check()
    assert source_dir not in str(failure.value)
    factory.assert_not_called()


@pytest.mark.parametrize(
    "factory_path", ["", "missing_readiness_module.factory", "readiness_fixture_factory.missing"]
)
def test_preflight_reports_unconfigured_or_unimportable_factory(
    agent_check, settings, factory_path
):
    settings.REDORDA_ENVIRONMENT_FACTORY = factory_path
    with pytest.raises(CommandError, match="factory"):
        invoke_check()


def test_preflight_redacts_factory_errors_and_output(agent_check, capsys):
    _, _, factory = agent_check

    def broken(context):
        print("private-upstream-token")
        print("private-upstream-token", file=sys.stderr)
        raise RuntimeError("private-upstream-token")

    factory.side_effect = broken
    with pytest.raises(CommandError, match="validation failed") as failure:
        invoke_check()
    output = capsys.readouterr()
    assert "private-upstream-token" not in str(failure.value) + output.out + output.err


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("remaining_budget", 99999),
        ("remaining_budget", float("nan")),
        ("remaining_contacts", 14999),
        ("pilots_left", 19),
        ("pilot_history", [{"cost": 0, "n_customers": 1}]),
        ("run_pilot", None),
    ],
)
def test_preflight_rejects_nonfresh_or_incomplete_environment(agent_check, attribute, value):
    _, environment, _ = agent_check
    setattr(environment, attribute, value)
    with pytest.raises(CommandError, match="validation failed"):
        invoke_check()


@pytest.mark.parametrize("defect", ["profile", "count", "tariff", "channels"])
def test_preflight_validates_profile_catalogues_and_imported_count(agent_check, defect):
    dataset, environment, _ = agent_check
    if defect == "profile":
        environment.customer_profile.loc[0, "predicted_arpu"] = -1
    elif defect == "count":
        dataset.customer_count = 99
        dataset.save(update_fields=["customer_count"])
    elif defect == "tariff":
        environment.tariffs.loc[0, "tariff_plan_code"] = "tariff_99"
    else:
        environment.channels["sms"]["cost_per_contact"] = 400
    with pytest.raises(CommandError, match="validation failed"):
        invoke_check()


def test_preflight_absent_key_and_custom_model_are_reported_without_values(
    agent_check, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "private-config-value\nsecret")
    stdout, _ = invoke_check()
    assert "OpenAI key configured: false; model: custom" in stdout
    assert "private-config-value" not in stdout
