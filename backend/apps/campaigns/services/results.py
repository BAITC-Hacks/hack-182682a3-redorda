"""Read saved campaign results without executing the strategy again."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from campaign_engine.contracts import CASE_LIMITS, Campaign
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from pydantic import ValidationError


class ResultNotReady(Exception):
    """The run has not completed and cannot be presented or exported."""


class InvalidSavedResult(ValueError):
    """Persisted output violates the public campaign contract."""


_TARIFF = re.compile(r"tariff_(?:[1-9]|1[0-9]|2[01])\Z")
_CENT = Decimal("0.01")


def _money(value, field):
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidSavedResult(f"{field} must be a finite nonnegative amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidSavedResult(f"{field} must be a finite nonnegative amount") from exc
    if not amount.is_finite() or amount < 0:
        raise InvalidSavedResult(f"{field} must be a finite nonnegative amount")
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def _campaign(value):
    try:
        campaign = Campaign.model_validate(value).model_dump(exclude_none=True)
    except (ValidationError, TypeError) as exc:
        raise InvalidSavedResult(f"Invalid saved campaign: {exc}") from exc
    current = campaign.get("filter_current_tariff")
    if current is not None and (
        not current.strip()
        or any(not _TARIFF.fullmatch(part.strip()) for part in current.split(";"))
    ):
        raise InvalidSavedResult("Invalid filter_current_tariff")
    return campaign


def _saved_result(run_id, *, require_completed=True):
    Run = apps.get_model("campaigns", "CampaignRun")
    run = Run.objects.get(pk=run_id)
    if require_completed and run.status != Run.Status.COMPLETED:
        raise ResultNotReady(f"Run {run_id} is {run.status}")
    try:
        result = run.result
    except ObjectDoesNotExist as exc:
        raise InvalidSavedResult("Completed run has no RunResult") from exc
    rows = list(run.campaign_results.all().order_by("rank"))
    if not 1 <= len(rows) <= CASE_LIMITS["campaigns"]:
        raise InvalidSavedResult("A completed run needs 1–10 campaigns")
    for rank, row in enumerate(rows, 1):
        if row.rank != rank:
            raise InvalidSavedResult("Campaign ranks must be consecutive from 1")
    return run, result, rows


def get_results(run_id) -> dict:
    """Return a stable API representation from persisted rows and summary."""
    return _validated_results(run_id, require_completed=True)


def validate_results(run_id):
    """Validate saved output before the worker commits a completed status."""
    _validated_results(run_id, require_completed=False)


def _validated_results(run_id, *, require_completed):
    run, result, rows = _saved_result(run_id, require_completed=require_completed)
    summary = result.summary or {}
    if not isinstance(summary, dict):
        raise InvalidSavedResult("RunResult.summary must be an object")
    campaigns = []
    campaign_costs = []
    campaign_contacts = []
    for row in rows:
        campaign = _campaign(row.campaign)
        metrics = row.metrics if row.metrics is not None else {}
        if not isinstance(metrics, dict):
            raise InvalidSavedResult("Campaign metrics must be an object")
        metrics = {**metrics, "cost": metrics.get("cost"),
                   "n_contacts": metrics.get("n_contacts")}
        cost = _money(metrics.get("cost"), "campaign cost")
        if cost is not None:
            campaign_costs.append(cost)
            metrics = {**metrics, "cost": str(cost)}
        contacts = metrics.get("n_contacts")
        if contacts is not None:
            if (isinstance(contacts, bool) or not isinstance(contacts, int)
                    or not 0 <= contacts <= CASE_LIMITS["customers_per_campaign"]):
                raise InvalidSavedResult("Invalid campaign n_contacts")
            campaign_contacts.append(contacts)
        campaigns.append({"rank": row.rank, "parameters": campaign,
                          "explanation": row.explanation or None, "metrics": metrics})

    pilots = list(run.pilots.all().order_by("sequence"))
    if len(pilots) > min(run.max_pilots, CASE_LIMITS["pilots"]):
        raise InvalidSavedResult("Pilot count exceeds limit")
    pilot_cost = Decimal("0.00")
    pilot_contacts = 0
    for pilot in pilots:
        cost = _money(pilot.cost, "pilot cost")
        if cost is None or not 1 <= pilot.n_customers <= CASE_LIMITS["pilot_max_customers"]:
            raise InvalidSavedResult("Invalid saved pilot")
        pilot_cost += cost
        pilot_contacts += pilot.n_customers
    if pilot_contacts > min(run.max_contacts, CASE_LIMITS["contacts"]):
        raise InvalidSavedResult("Pilot contacts exceed limit")
    if pilot_cost + sum(campaign_costs, Decimal(0)) > _money(run.budget, "run budget"):
        raise InvalidSavedResult("Known spend exceeds run budget")
    if pilot_contacts + sum(campaign_contacts) > min(run.max_contacts, CASE_LIMITS["contacts"]):
        raise InvalidSavedResult("Known contacts exceed limit")

    campaign_cost = (sum(campaign_costs, Decimal("0.00"))
                     if len(campaign_costs) == len(rows) else None)
    total_cost = pilot_cost + campaign_cost if campaign_cost is not None else None
    if total_cost is not None and total_cost > _money(run.budget, "run budget"):
        raise InvalidSavedResult("Total spend exceeds run budget")
    total_contacts = (pilot_contacts + sum(campaign_contacts)
                      if len(campaign_contacts) == len(rows) else None)
    if (total_contacts is not None
            and total_contacts > min(run.max_contacts, CASE_LIMITS["contacts"])):
        raise InvalidSavedResult("Total contacts exceed limit")

    warnings = summary.get("warnings") or []
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise InvalidSavedResult("RunResult warnings must be a list of strings")
    warnings = list(warnings)
    if campaign_cost is None:
        warnings.append("Campaign costs are incomplete; total spend is unknown.")
    if total_contacts is None:
        warnings.append("Campaign contact counts are incomplete; total contacts are unknown.")
    if any(campaign["explanation"] is None for campaign in campaigns):
        warnings.append("Explanations are missing for some campaigns.")
    predicted = summary.get("predicted_effect")
    simulator = summary.get("simulator_result")
    if predicted is None:
        warnings.append("Predicted effect was not calculated.")
    if simulator is None:
        warnings.append("Simulator result was not calculated.")
    return {
        "run_id": str(run.id), "status": run.status, "campaigns": campaigns,
        "totals": {
            "pilot_cost": str(pilot_cost),
            "campaign_cost": str(campaign_cost) if campaign_cost is not None else None,
            "total_cost": str(total_cost) if total_cost is not None else None,
            "pilot_contacts": pilot_contacts, "total_contacts": total_contacts,
            "predicted_effect": predicted, "simulator_result": simulator,
        },
        "warnings": warnings,
    }


def iter_submission_csv(run_id):
    """Public export entry point kept alongside get_results."""
    from .export import iter_submission_csv as stream_csv

    yield from stream_csv(run_id)
