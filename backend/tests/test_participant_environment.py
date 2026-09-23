import os
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest
from apps.campaigns.services.engine_bridge import EngineContext
from apps.campaigns.services.execution import ExecutionUnavailable
from apps.campaigns.services.participant_environment import create_environment

FAKE_FACTORY = '''
from types import SimpleNamespace
import pandas as pd
from environment import MARKER
from scoring_core import CHANNELS

def make_mock_env(seed=None):
    profile = pd.read_csv("customer_profile.csv")
    env = SimpleNamespace(
        customer_profile=profile, tariffs=pd.read_csv("data/dict_tariff.csv"),
        channels=CHANNELS, remaining_budget=100000, remaining_contacts=15000,
        pilots_left=20, pilot_history=[], marker=MARKER, seed=seed,
        run_pilot=lambda **kwargs: None,
    )
    return env, object()
'''


def _kit(directory, arpu=100):
    directory.mkdir()
    (directory / "data").mkdir()
    pd.DataFrame({
        "ID_NUMBER": [1, 2], "predicted_arpu": [arpu, arpu],
        "current_tariff": ["tariff_1"] * 2, "arpu_segment": ["LOW"] * 2,
        "data_segment": ["LITE"] * 2, "call_segment": ["LOW"] * 2,
    }).to_csv(directory / "customer_profile.csv", index=False)
    pd.DataFrame({
        "tariff_plan_code": ["tariff_1", "tariff_2"], "price_tariff": [100., 200.],
        "Data_in_PKG": [1, 2], "Min_another_operator_in_PKG": [0, 20],
        "Min_another_operator_and_city_in_PKG": [0, 20],
    }).to_csv(
        directory / "data/dict_tariff.csv", index=False)
    (directory / "data/change_tariff.csv").write_text("unused\n")
    (directory / "environment.py").write_text(f"MARKER = {arpu!r}\n")
    (directory / "scoring_core.py").write_text(
        "CHANNELS = {key: {'cost_per_contact': cost} for key, cost in "
        "{'push': 0, 'sms': 4, 'digital_ads': 22, 'call': 160}.items()}\n")
    (directory / "mock_environment.py").write_text(FAKE_FACTORY)
    return directory


def _context(path, seed=9):
    return EngineContext(path, Decimal("40000"), 9000, 3, seed, "baseline")


def test_uses_selected_dataset_and_restores_process_imports(tmp_path, monkeypatch):
    kit = _kit(tmp_path / "kit", arpu=120)
    shadow = ModuleType("environment")
    shadow.MARKER = "unrelated module"
    monkeypatch.setitem(sys.modules, "environment", shadow)
    cwd, search, bytecode = Path.cwd(), sys.path[:], sys.dont_write_bytecode
    env = create_environment(_context(kit, seed=17))
    assert env.marker == 120 and env.seed == 17
    assert env.remaining_budget == 100000 and env.remaining_contacts == 15000
    assert env.customer_profile.predicted_arpu.sum() == 240
    assert Path.cwd() == cwd and sys.path == search and sys.dont_write_bytecode == bytecode
    assert sys.modules["environment"] is shadow
    assert not (kit / "__pycache__").exists()


def test_two_datasets_do_not_reuse_module_or_csv_state(tmp_path):
    first = create_environment(_context(_kit(tmp_path / "first", arpu=100)))
    second = create_environment(_context(_kit(tmp_path / "second", arpu=900)))
    assert first.marker == 100 and second.marker == 900
    assert first.customer_profile.predicted_arpu.sum() == 200
    assert second.customer_profile.predicted_arpu.sum() == 1800


def test_failed_factory_restores_cwd_and_has_safe_error(tmp_path):
    kit = _kit(tmp_path / "broken")
    (kit / "mock_environment.py").write_text("raise RuntimeError('private-value')\n")
    cwd, search = Path.cwd(), sys.path[:]
    with pytest.raises(ExecutionUnavailable, match="could not be loaded") as failure:
        create_environment(_context(kit))
    assert "private-value" not in str(failure.value) and str(kit) not in str(failure.value)
    assert Path.cwd() == cwd and sys.path == search


def test_rejects_profile_from_a_different_dataset(tmp_path):
    kit = _kit(tmp_path / "wrong")
    (kit / "mock_environment.py").write_text(FAKE_FACTORY.replace(
        'env = SimpleNamespace(', 'profile["predicted_arpu"] = 999\n    env = SimpleNamespace('))
    with pytest.raises(ExecutionUnavailable, match="could not be loaded"):
        create_environment(_context(kit))


def test_rejects_changed_tariff_price_with_same_tariff_codes(tmp_path):
    kit = _kit(tmp_path / "wrong-tariffs")
    (kit / "mock_environment.py").write_text(FAKE_FACTORY.replace(
        "return env, object()", 'env.tariffs["price_tariff"] = 999\n    return env, object()'))
    with pytest.raises(ExecutionUnavailable, match="could not be loaded"):
        create_environment(_context(kit))


@pytest.mark.parametrize("error", ["TimeoutError", "SoftTimeLimitExceeded"])
def test_timeout_propagates_and_restores_process_state(tmp_path, error):
    kit = _kit(tmp_path / "timed-out")
    (kit / "mock_environment.py").write_text(
        f"from billiard.exceptions import SoftTimeLimitExceeded\nraise {error}()\n")
    from billiard.exceptions import SoftTimeLimitExceeded
    expected = TimeoutError if error == "TimeoutError" else SoftTimeLimitExceeded
    cwd, search = Path.cwd(), sys.path[:]
    with pytest.raises(expected):
        create_environment(_context(kit))
    assert Path.cwd() == cwd and sys.path == search


def test_missing_kit_does_not_import_unrelated_modules(tmp_path, monkeypatch):
    shadow = ModuleType("mock_environment")
    shadow.make_mock_env = lambda **kwargs: pytest.fail("Unrelated factory was called")
    monkeypatch.setitem(sys.modules, "mock_environment", shadow)
    with pytest.raises(ExecutionUnavailable, match="complete participant kit"):
        create_environment(_context(tmp_path))
    with pytest.raises(ExecutionUnavailable, match="absolute"):
        create_environment(_context(Path("relative")))


def test_installed_public_environment_pilot_survives_cwd_restoration(tmp_path):
    kit = Path(__file__).resolve().parents[2] / "data/participant-kit"
    if not (kit / "mock_environment.py").is_file():
        pytest.skip("Participant kit is not installed")
    original_directory = Path.cwd()
    env = create_environment(_context(kit, seed=42))
    try:
        os.chdir(tmp_path)
        observed = env.run_pilot(target_tariff="tariff_6", channel="sms", n_customers=200,
                                 filter_current_tariff="tariff_8", filter_arpu_segment="HIGH")
    finally:
        os.chdir(original_directory)
    assert observed["n_customers"] == 200 and observed["cost"] == 800
    assert env.remaining_budget == 99200 and env.remaining_contacts == 14800
    assert env.pilots_left == 19 and len(env.pilot_history) == 1
