"""Connect an imported participant dataset to its public simulation factory."""

import importlib
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
from billiard.exceptions import SoftTimeLimitExceeded
from campaign_engine.contracts import CASE_LIMITS, CHANNEL_COSTS
from campaign_engine.segments import SegmentIndex

from .execution import ExecutionUnavailable

_MODULES = ("environment", "scoring_core", "mock_environment")
_PROFILE_COLUMNS = (
    "ID_NUMBER", "predicted_arpu", "current_tariff", "arpu_segment",
    "data_segment", "call_segment",
)
_TARIFF_COLUMNS = (
    "tariff_plan_code", "price_tariff", "Data_in_PKG",
    "Min_another_operator_in_PKG", "Min_another_operator_and_city_in_PKG",
)
_IMPORT_LOCK = threading.RLock()


@contextmanager
def _participant_imports(directory):
    """Scope the kit's relative imports and CSV paths to one synchronous factory call.

    Workers use prefork. The returned public environment owns its state in memory;
    subsequent pilots do not depend on the worker's working directory.
    """
    with _IMPORT_LOCK:
        previous_directory = Path.cwd()
        previous_path = sys.path[:]
        previous_modules = {name: sys.modules.get(name) for name in _MODULES}
        previous_bytecode = sys.dont_write_bytecode
        try:
            for name in _MODULES:
                sys.modules.pop(name, None)
            sys.path.insert(0, str(directory))
            sys.dont_write_bytecode = True
            os.chdir(directory)
            importlib.invalidate_caches()
            yield
        finally:
            os.chdir(previous_directory)
            sys.path[:] = previous_path
            sys.dont_write_bytecode = previous_bytecode
            for name, previous in previous_modules.items():
                sys.modules.pop(name, None)
                if previous is not None:
                    sys.modules[name] = previous


def create_environment(context):
    """Create a fresh simulator using only make_mock_env's public entry point.

    The dataset path comes from the saved Dataset, never from a browser request.
    Configured run limits are enforced by the shared runner against the full
    public environment limits. Evaluation internals are not passed to the agent.
    """
    directory = Path(context.dataset_path)
    if not directory.is_absolute():
        raise ExecutionUnavailable("The imported dataset needs an absolute source directory")
    directory = directory.resolve()
    required = [*(f"{name}.py" for name in _MODULES), "customer_profile.csv",
                "data/dict_tariff.csv", "data/change_tariff.csv"]
    if any(not (directory / name).is_file() for name in required):
        raise ExecutionUnavailable("Install the complete participant kit for the imported dataset")
    try:
        with _participant_imports(directory):
            module = importlib.import_module("mock_environment")
            if Path(module.__file__).resolve() != directory / "mock_environment.py":
                raise ValueError("Unexpected simulation module")
            environment, _ = module.make_mock_env(seed=context.seed)
        expected = pd.read_csv(directory / "customer_profile.csv")
        index = SegmentIndex(environment.customer_profile)
        expected_index = SegmentIndex(expected)
        columns = list(_PROFILE_COLUMNS)
        if not index.profile[columns].equals(expected_index.profile[columns]):
            raise ValueError("Environment profile differs from the selected dataset")
        expected_tariffs = pd.read_csv(directory / "data/dict_tariff.csv")
        columns = list(_TARIFF_COLUMNS)
        actual_tariffs = environment.tariffs[columns].sort_values(
            "tariff_plan_code").reset_index(drop=True)
        expected_tariffs = expected_tariffs[columns].sort_values(
            "tariff_plan_code").reset_index(drop=True)
        if not actual_tariffs.equals(expected_tariffs):
            raise ValueError("Environment tariffs differ from the selected dataset")
        if (environment.remaining_budget != CASE_LIMITS["budget"]
                or environment.remaining_contacts != CASE_LIMITS["contacts"]
                or environment.pilots_left != CASE_LIMITS["pilots"]
                or environment.pilot_history):
            raise ValueError("A fresh full-budget environment is required")
        for channel, cost in CHANNEL_COSTS.items():
            if environment.channels[channel]["cost_per_contact"] != cost:
                raise ValueError("Environment channel costs differ from the public contract")
        if not callable(environment.run_pilot):
            raise ValueError("Public run_pilot is missing")
        return environment
    except (TimeoutError, SoftTimeLimitExceeded):
        raise
    except Exception as exc:
        raise ExecutionUnavailable(
            "The participant simulation environment could not be loaded") from exc
