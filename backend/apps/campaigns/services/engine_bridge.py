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
from .team_state import persist_team_event


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
        self._before_step()
        return getattr(self._delegate, name)

    def run_pilot(self, **kwargs):
        self._before_step()
        self._before_pilot(kwargs)
        response = self._delegate.run_pilot(**kwargs)
        self._on_pilot(kwargs, response)
        self._before_step()
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

    ``runner`` is the legacy test seam. Production uses the shared runner.
    """
    constraints = run.constraints or {}
    if not isinstance(constraints, dict):
        raise ExecutionUnavailable("Saved constraints have an invalid format")
    unsupported = set(constraints) - {"budget", "allowed_channels"}
    if unsupported:
        raise ExecutionUnavailable("Unsupported run constraint: " + ", ".join(sorted(unsupported)))
    if "budget" in constraints:
        try:
            same_budget = Decimal(str(constraints["budget"])) == run.budget
        except (InvalidOperation, TypeError, ValueError):
            same_budget = False
        if not same_budget:
            raise ExecutionUnavailable("Saved budget constraint differs from the run budget")
    if "allowed_channels" in constraints and "allowed_channels" not in RunConfig.model_fields:
        raise ExecutionUnavailable("The campaign engine does not support allowed_channels")
    if runner is not None and "allowed_channels" in constraints:
        raise ExecutionUnavailable("The supplied runner does not support allowed_channels")
    context = EngineContext(Path(run.dataset.source_dir), run.budget, run.max_contacts,
                            run.max_pilots, run.seed, run.strategy)
    environment = _public_environment(context) if runner is None else runner.environment(context)
    index = SegmentIndex(environment.customer_profile) if runner is None else None
    expected_pilot_size = None

    def before_step():
        if check_cancel():
            raise ExecutionCancelled()

    def before_pilot(request):
        nonlocal expected_pilot_size
        count = request.get("n_customers", 100)
        channel = request.get("channel")
        if (type(count) is not int
                or not CASE_LIMITS["pilot_min_customers"] <= count
                <= CASE_LIMITS["pilot_max_customers"]
                or channel not in CHANNEL_COSTS):
            raise ValueError("Invalid pilot size or channel")
        pilots = list(Pilot.objects.filter(run=run))
        if len(pilots) >= min(run.max_pilots, CASE_LIMITS["pilots"]):
            raise ValueError("Pilot count would exceed limit")
        if index is not None:
            count = min(count, len(index.positions_for(request)), environment.remaining_contacts)
            if CHANNEL_COSTS[channel]:
                count = min(count, int(environment.remaining_budget // CHANNEL_COSTS[channel]))
            if count < 1 or environment.pilots_left < 1:
                raise ValueError("Pilot has no available audience or resources")
        expected_pilot_size = count
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
                or (index is not None and n_customers != expected_pilot_size)
                or cost != n_customers * CHANNEL_COSTS[request["channel"]]):
            raise ExecutionUnavailable("Pilot response violates the public cost/contact contract")
        with transaction.atomic():
            current = CampaignRun.objects.select_for_update().get(pk=run.pk)
            if current.status != CampaignRun.Status.RUNNING:
                raise ExecutionCancelled()
            sequence = Pilot.objects.filter(run=run).count() + 1
            Pilot.objects.create(run=run, sequence=sequence, request=_json(request),
                                 response=_json(response), cost=cost, n_customers=n_customers)
            payload = {"sequence": sequence, "cost": str(cost), "n_customers": n_customers,
                       "requested_customers": request.get("n_customers", 100),
                       "channel": request["channel"]}
            if runner is None:
                payload.update({"request": _json(request), "observation": _json({
                    key: response[key] for key in (
                        "n_customers", "cost", "observed_lift_ratio", "observed_lift_total",
                        "remaining_budget", "remaining_contacts") if key in response
                })})
            RunEvent.objects.create(run=run, kind="pilot_completed",
                                    payload=payload)

    def observe(event):
        kind, payload = event["type"], event["data"]
        if kind in {"task_started", "task_completed", "task_failed", "task_handoff"}:
            persist_team_event(run_id=run.pk, kind=kind, payload=_json(payload))
            return
        if kind == "run_failed":
            payload = {"reason": "engine_failed"}
        if kind == "pilot_completed":
            kind = "pilot_estimate_updated"
            payload = {"sequence": Pilot.objects.filter(run=run).latest("sequence").sequence,
                       "posterior": payload["posterior"]}
        RunEvent.objects.create(run=run, kind=kind, payload=_json(payload))

    before_step()
    observed = ObservedEnvironment(environment, before_step=before_step,
                                   before_pilot=before_pilot, on_pilot=on_pilot)
    try:
        if runner is None:
            config = RunConfig(budget=float(run.budget), max_contacts=run.max_contacts,
                               max_pilots=run.max_pilots, seed=run.seed, strategy=run.strategy,
                               **({"allowed_channels": constraints["allowed_channels"]}
                                  if "allowed_channels" in constraints else {}))
            result = run_campaigns(observed, config, history=load_history(context.dataset_path),
                                   observer=observe, should_cancel=check_cancel)
            before_step()
            if result.status == "cancelled":
                raise ExecutionCancelled()
            if result.status != "completed":
                raise EngineFailure(result.stop_reason)
            details = result.estimates.get("campaigns", [])
            if len(details) != len(result.campaigns):
                raise EngineFailure("Campaign metrics are incomplete")
            output = {"campaigns": [
                {"campaign": campaign, "metrics": metrics,
                 "explanation": "Прогноз рассчитан по историческим данным и наблюдениям пилотов."}
                for campaign, metrics in zip(result.campaigns, details, strict=True)
            ], "summary": {
                "predicted_effect": {key: result.estimates.get(key) for key in (
                    "source", "net_arpu_gain_mean", "lower_tail_mean_10", "objective")},
                "simulator_result": None,
                "warnings": [*result.warnings, *result.estimates.get("limitations", [])],
                "estimates": result.estimates,
                "resource_usage": result.resource_usage,
                "metadata": {key: result.metadata[key] for key in (
                    "engine_version", "seed", "strategy", "options", "hypothesis_source",
                    "elapsed_seconds") if key in result.metadata},
                "stop_reason": result.stop_reason,
            }}
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
