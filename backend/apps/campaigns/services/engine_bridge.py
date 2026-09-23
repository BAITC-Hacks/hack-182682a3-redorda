"""Adapter between Django persistence and the shared, Django-free agent."""

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from campaign_engine.agent import Agent
from campaign_engine.contracts import CASE_LIMITS, CHANNEL_COSTS, Campaign
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

    ``runner`` exists only for tests. Production always constructs Agent and a
    configured adapter to the organizer's public environment.
    """
    context = EngineContext(Path(run.dataset.source_dir), run.budget, run.max_contacts,
                            run.max_pilots, run.seed, run.strategy)
    environment = _public_environment(context) if runner is None else runner.environment(context)

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
            RunEvent.objects.create(run=run, kind="pilot_completed",
                                    payload={"sequence": sequence, "cost": str(cost),
                                             "n_customers": n_customers,
                                             "requested_customers": request.get("n_customers", 100),
                                             "channel": request["channel"]})

    before_step()
    observed = ObservedEnvironment(environment, before_step=before_step,
                                   before_pilot=before_pilot, on_pilot=on_pilot)
    try:
        output = Agent().act(observed) if runner is None else runner.act(observed)
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
