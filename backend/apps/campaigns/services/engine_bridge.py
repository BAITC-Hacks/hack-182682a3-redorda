"""Adapter between Django persistence and the shared, Django-free agent."""

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from campaign_engine.candidates import load_history
from campaign_engine.contracts import CASE_LIMITS, CHANNEL_COSTS, Campaign, RunConfig
from campaign_engine.engine_types import EngineFailure
from campaign_engine.runner import run_campaigns
from campaign_engine.segments import SegmentIndex
from django.conf import settings
from django.db import transaction
from django.utils.module_loading import import_string

from apps.campaigns.models import CampaignResult, CampaignRun, Pilot, RunEvent, RunResult

from .execution import ExecutionUnavailable


class ExecutionCancelled(Exception):
    """The user requested a cooperative stop."""


@dataclass(frozen=True)
class EngineContext:
    dataset_path: Path
    budget: Decimal
    max_contacts: int
    max_pilots: int
    seed: int
    strategy: str


def _json(value):
    return json.loads(json.dumps(value, default=str, allow_nan=False))


class ObservedEnvironment:
    def __init__(self, delegate, *, before_step, before_pilot, on_pilot):
        self._delegate = delegate
        self._before_step = before_step
        self._before_pilot = before_pilot
        self._on_pilot = on_pilot

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def run_pilot(self, **kwargs):
        self._before_step()
        self._before_pilot(kwargs)
        response = self._delegate.run_pilot(**kwargs)
        self._on_pilot(kwargs, response)
        return response


def _public_environment(context):
    factory_path = getattr(settings, "REDORDA_ENVIRONMENT_FACTORY", None) or os.getenv(
        "REDORDA_ENVIRONMENT_FACTORY")
    if not factory_path:
        raise ExecutionUnavailable("Public organizer environment is not connected")
    try:
        factory = import_string(factory_path)
        return factory(context)
    except (ImportError, AttributeError, FileNotFoundError) as exc:
        raise ExecutionUnavailable("Public organizer environment is unavailable") from exc


def run_engine(run, *, check_cancel, runner=None):
    """Run the actual agent, persisting each observed pilot and final result.

    ``runner`` is a legacy test seam. Production calls the same shared runner as
    Agent.act, with this plan's configuration and the imported dataset history.
    """
    context = EngineContext(Path(run.dataset.source_dir), run.budget, run.max_contacts,
                            run.max_pilots, run.seed, run.strategy)
    environment = _public_environment(context) if runner is None else runner.environment(context)
    index = SegmentIndex(environment.customer_profile) if runner is None else None

    def before_step():
        if check_cancel():
            raise ExecutionCancelled()

    def before_pilot(request):
        count = request.get("n_customers", 100)
        channel = request.get("channel")
        if (type(count) is not int
                or not CASE_LIMITS["pilot_min_customers"] <= count
                <= CASE_LIMITS["pilot_max_customers"]
                or channel not in CHANNEL_COSTS):
            raise ValueError("Invalid pilot size or channel")
        # The public environment caps a requested sample to eligible customers
        # and available resources. Guard the actual charge, not the upper bound.
        if index is not None:
            count = min(count, len(index.positions_for(request)), environment.remaining_contacts)
            if CHANNEL_COSTS[channel]:
                count = min(count, int(environment.remaining_budget // CHANNEL_COSTS[channel]))
            if count <= 0:
                raise ValueError("Pilot audience is empty")
        pilots = list(Pilot.objects.filter(run=run))
        if len(pilots) >= min(run.max_pilots, CASE_LIMITS["pilots"]):
            raise ValueError("Pilot count would exceed limit")
        if sum(pilot.n_customers for pilot in pilots) + count > run.max_contacts:
            raise ValueError("Pilot contacts would exceed limit")
        if sum((pilot.cost for pilot in pilots), Decimal(0)) + count * CHANNEL_COSTS[channel] > (
            run.budget
        ):
            raise ValueError("Pilot cost would exceed budget")

    def on_pilot(request, response):
        if not isinstance(response, dict):
            raise ExecutionUnavailable("Pilot response has an unsupported format")
        try:
            cost = Decimal(str(response["cost"]))
            n_customers = response["n_customers"]
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise ExecutionUnavailable("Pilot response lacks cost or customer count") from exc
        if (not cost.is_finite() or cost < 0 or type(n_customers) is not int
                or not 1 <= n_customers <= request.get("n_customers", 100)
                or cost != n_customers * CHANNEL_COSTS[request["channel"]]):
            raise ExecutionUnavailable("Pilot response violates the public cost/contact contract")
        with transaction.atomic():
            current = CampaignRun.objects.select_for_update().get(pk=run.pk)
            if current.status != CampaignRun.Status.RUNNING:
                raise ExecutionCancelled()
            sequence = Pilot.objects.filter(run=run).count() + 1
            Pilot.objects.create(run=run, sequence=sequence, request=_json(request),
                                 response=_json(response), cost=cost, n_customers=n_customers)
            if runner is not None:
                RunEvent.objects.create(run=run, kind="pilot_completed",
                                        payload={"sequence": sequence, "cost": str(cost),
                                                 "n_customers": n_customers,
                                                 "requested_customers": request.get(
                                                     "n_customers", 100),
                                                 "channel": request["channel"]})

    def observe(event):
        # Engine error strings can contain upstream exception details. Publish
        # a stable reason; the task stores internal diagnostics separately.
        payload = event["data"]
        if event["type"] == "run_failed":
            payload = {"reason": "execution_failed"}
        RunEvent.objects.create(run=run, kind=event["type"], payload=_json(payload))

    before_step()
    observed = ObservedEnvironment(environment, before_step=before_step,
                                   before_pilot=before_pilot, on_pilot=on_pilot)
    try:
        if runner is None:
            config = RunConfig(budget=float(run.budget), max_contacts=run.max_contacts,
                               max_pilots=run.max_pilots, seed=run.seed, strategy=run.strategy)
            result = run_campaigns(observed, config, history=load_history(context.dataset_path),
                                   observer=observe, should_cancel=check_cancel)
            before_step()  # Preserve cancellation/timeouts even if the runner caught them.
            if result.status == "cancelled":
                raise ExecutionCancelled()
            if result.status != "completed":
                raise EngineFailure(result.stop_reason)
            output = _saved_output(result)
        else:
            output = runner.act(observed)
    except NotImplementedError as exc:
        raise ExecutionUnavailable("Campaign engine is not implemented") from exc
    before_step()
    summary = {}
    campaigns = output
    if isinstance(output, dict):
        campaigns = output.get("campaigns")
        summary = output.get("summary", {})
    if not isinstance(campaigns, list) or not 1 <= len(campaigns) <= CASE_LIMITS["campaigns"]:
        raise ExecutionUnavailable("Campaign engine must return 1–10 campaigns")
    for rank, item in enumerate(campaigns, start=1):
        before_step()
        if not isinstance(item, dict):
            raise ExecutionUnavailable("Campaign engine returned an invalid campaign")
        campaign_data = item.get("campaign", item)
        campaign = Campaign.model_validate(campaign_data).to_submission()
        metrics = item.get("metrics", {})
        explanation = item.get("explanation", "")
        with transaction.atomic():
            current = CampaignRun.objects.select_for_update().get(pk=run.pk)
            if current.status != CampaignRun.Status.RUNNING or current.cancel_requested:
                raise ExecutionCancelled()
            CampaignResult.objects.create(run=run, rank=rank, campaign=_json(campaign),
                                          metrics=_json(metrics), explanation=str(explanation))
            RunEvent.objects.create(run=run, kind="campaign_result", payload={"rank": rank})
    before_step()
    with transaction.atomic():
        current = CampaignRun.objects.select_for_update().get(pk=run.pk)
        if current.status != CampaignRun.Status.RUNNING or current.cancel_requested:
            raise ExecutionCancelled()
        RunResult.objects.create(run=run, summary=_json(summary))
        RunEvent.objects.create(run=run, kind="result_ready",
                                payload={"campaigns": len(campaigns)})
        from .results import validate_results

        validate_results(run.pk)


def _saved_output(result):
    """Map the shared runner's forecast to the stable HTTP result contract."""
    details = {item["campaign_name"]: item for item in result.estimates["campaigns"]}
    campaigns = []
    for campaign in result.campaigns:
        detail = details[campaign["campaign_name"]]
        campaigns.append({
            "campaign": campaign,
            "metrics": {"cost": str(Decimal(str(detail["cost"])).quantize(Decimal(".01"))),
                        "n_contacts": detail["n_contacts"],
                        "predicted_effect": str(detail["estimated_incremental_net"]),
                        "forecast_source": result.estimates["source"]},
            "explanation": (
                "Кампания выбрана по прогнозу прироста выручки за вычетом расходов "
                "с учётом пилотов, неопределённости эффекта и лимитов плана. "
                "Прогноз не является результатом симуляции."
            ),
        })
    return {"campaigns": campaigns, "summary": {
        "predicted_effect": str(result.estimates["net_arpu_gain_mean"]),
        "simulator_result": None,
        "warnings": result.warnings + result.estimates.get("limitations", []),
        "engine": result.to_dict(),
    }}
