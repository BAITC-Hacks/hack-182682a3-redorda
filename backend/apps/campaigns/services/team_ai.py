"""Adapter for the campaign engine's saved-snapshot analysis functions."""

from dataclasses import asdict, is_dataclass
from importlib import import_module


class CapabilityUnavailable(Exception):
    """The engine has not supplied this saved-snapshot operation yet."""


def _function(name):
    for path in ("campaign_engine.team_analysis", "campaign_engine.team_ai",
                 "campaign_engine.team"):
        try:
            module = import_module(path)
        except ModuleNotFoundError as exc:
            if exc.name != path:
                raise
            continue
        function = getattr(module, name, None)
        if callable(function):
            return function
    raise CapabilityUnavailable(f"AI capability {name} is unavailable")


def _invoke(name, *args):
    result = _function(name)(*args)
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if is_dataclass(result):
        return asdict(result)
    raise TypeError(f"AI capability {name} returned an unsupported result")


def explain(snapshot, campaign_id):
    return _invoke("explain", snapshot, campaign_id)


def compare(snapshot, constraints):
    return _invoke("compare", snapshot, constraints)
