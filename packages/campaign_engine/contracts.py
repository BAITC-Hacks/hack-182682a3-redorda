"""Stable integration contracts. Coordinate changes with all three developers."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

Channel = Literal["push", "sms", "digital_ads", "call"]
ARPU = Literal["LOW", "MID", "HIGH"]
DataSegment = Literal["NON_USER", "LITE", "HEAVY"]
CallSegment = Literal["LOW", "MEDIUM", "HIGH"]

CASE_LIMITS = {
    "budget": 100_000,
    "contacts": 15_000,
    "campaigns": 10,
    "customers_per_campaign": 5_000,
    "pilots": 20,
    "pilot_min_customers": 10,
    "pilot_max_customers": 200,
    "runtime_seconds": 600,
}
CHANNEL_COSTS = {"push": 0, "sms": 4, "digital_ads": 22, "call": 160}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Campaign(StrictModel):
    campaign_name: str = Field(min_length=1, max_length=120)
    target_tariff: str = Field(pattern=r"^tariff_(?:[1-9]|1[0-9]|2[01])$")
    channel: Channel
    filter_arpu_segment: ARPU | None = None
    filter_data_segment: DataSegment | None = None
    filter_call_segment: CallSegment | None = None
    filter_current_tariff: str | None = None

    def to_submission(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class RunConfig(StrictModel):
    budget: float = Field(default=100_000, gt=0, le=100_000)
    max_contacts: int = Field(default=15_000, ge=1, le=15_000)
    max_pilots: int = Field(default=20, ge=1, le=20)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    strategy: Literal["baseline", "openai"] = "baseline"


class Hypothesis(StrictModel):
    campaign: Campaign
    rationale: str = Field(min_length=1, max_length=1000)


class HypothesisBatch(StrictModel):
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=10)


class AgentEnvironment(Protocol):
    """Only public organizer APIs may be used by the strategy."""

    customer_profile: Any
    tariffs: Any
    channels: Any
    remaining_budget: float
    remaining_contacts: int
    pilots_left: int
    pilot_history: Any

    def run_pilot(self, **kwargs: Any) -> dict[str, Any]: ...
