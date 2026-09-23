"""Runner configuration, results, and exceptions."""

from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass(frozen=True)
class EngineOptions:
    policy: Literal[
        "adaptive", "evolve", "fixed_100", "fixed_200", "wide_100", "adaptive_choice_200"
    ] = "adaptive"
    runtime_seconds: float = 240.0
    pilot_contact_cap: int = 2000
    candidate_limit: int = 24
    scenario_count: int = 32
    risk_weight: float = 0.15
    screening_pilots: int = 8

    def __post_init__(self):
        import math

        if self.policy not in {
            "adaptive",
            "evolve",
            "fixed_100",
            "fixed_200",
            "wide_100",
            "adaptive_choice_200",
        }:
            raise ValueError("Unknown pilot policy")
        if not math.isfinite(self.runtime_seconds) or not 0 < self.runtime_seconds <= 300:
            raise ValueError("runtime_seconds must be in (0, 300]")
        if not 10 <= self.pilot_contact_cap <= 4000:
            raise ValueError("pilot_contact_cap must be in [10, 4000]")
        if not 1 <= self.candidate_limit <= 48 or not 8 <= self.scenario_count <= 128:
            raise ValueError("Invalid candidate/scenario count")
        if not 0 <= self.risk_weight <= 1 or not 1 <= self.screening_pilots <= 20:
            raise ValueError("Invalid risk/screening configuration")


@dataclass
class PilotRecord:
    request: dict
    observation: dict
    prior: dict
    posterior: dict


@dataclass
class EngineResult:
    status: Literal["completed", "cancelled", "failed"] = "failed"
    campaigns: list[dict] = field(default_factory=list)
    pilots: list[PilotRecord] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    resource_usage: dict = field(default_factory=dict)
    estimates: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    stop_reason: str = ""

    def to_dict(self) -> dict:
        """Serialize the result as a JSON-compatible dictionary."""
        return asdict(self)


class EngineFailure(RuntimeError):
    """The run did not produce a completed plan."""


class ObserverFailure(RuntimeError):
    """A persistence failure must never cause a paid action to be replayed."""
