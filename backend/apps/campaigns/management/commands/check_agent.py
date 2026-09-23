"""Read-only validation of the imported dataset and configured agent environment."""

import contextlib
import io
import math
import os
import re
from decimal import Decimal
from pathlib import Path

from campaign_engine.contracts import CHANNEL_COSTS, RunConfig
from campaign_engine.segments import SegmentIndex
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from apps.campaigns.models import Dataset
from apps.campaigns.services.engine_bridge import EngineContext


def validate_fresh_environment(environment, config, expected_customers):
    index = SegmentIndex(environment.customer_profile)
    if not index.cells or len(index.profile) != expected_customers:
        raise ValueError("Dataset profile does not match the import")
    tariffs = environment.tariffs["tariff_plan_code"].tolist()
    codes = set(tariffs)
    if len(codes) != len(tariffs) or len(codes) < 2:
        raise ValueError("Tariff catalogue requires unique alternatives")
    if any(
        not isinstance(code, str) or not re.fullmatch(r"tariff_(?:[1-9]|1[0-9]|2[01])", code)
        for code in codes
    ):
        raise ValueError("Invalid tariff catalogue")
    if any(cell[0] not in codes for cell in index.cells):
        raise ValueError("Profile and tariff catalogue disagree")
    budget = float(environment.remaining_budget)
    if not math.isfinite(budget) or budget != config.budget:
        raise ValueError("Environment budget is not fresh")
    if (
        environment.remaining_contacts != config.max_contacts
        or environment.pilots_left != config.max_pilots
        or not isinstance(environment.pilot_history, list)
        or environment.pilot_history
        or not callable(environment.run_pilot)
    ):
        raise ValueError("Environment resource counters are not fresh")
    for channel, expected_cost in CHANNEL_COSTS.items():
        entry = environment.channels[channel]
        cost = float(entry["cost_per_contact"])
        multiplier = float(entry["conversion_multiplier"])
        if cost != expected_cost or not math.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("Invalid channel catalogue")
    return len(index.profile), index.excluded_count


class Command(BaseCommand):
    help = "Check dataset, factory and fresh public counters without pilots or model requests."

    def handle(self, *args, **options):
        try:
            dataset = Dataset.objects.first()
        except Exception:
            raise CommandError(
                "Dataset registry is unavailable; check the database and migrations."
            ) from None
        if dataset is None:
            raise CommandError("Import a dataset before checking the agent.")
        source = Path(dataset.source_dir)
        if not source.is_absolute() or not source.is_dir():
            raise CommandError("Imported dataset directory is unavailable to this process.")
        factory_path = getattr(settings, "REDORDA_ENVIRONMENT_FACTORY", "")
        if not factory_path:
            raise CommandError("Agent environment factory is not configured.")
        try:
            factory = import_string(factory_path)
            if not callable(factory):
                raise TypeError("Configured factory is not callable")
        except Exception:
            raise CommandError("Agent environment factory cannot be imported.") from None
        config = RunConfig()
        context = EngineContext(
            dataset_path=source,
            budget=Decimal(str(config.budget)),
            max_contacts=config.max_contacts,
            max_pilots=config.max_pilots,
            seed=config.seed,
            strategy=config.strategy,
        )
        try:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                environment = factory(context)
                customers, excluded = validate_fresh_environment(
                    environment, config, dataset.customer_count
                )
        except Exception:
            raise CommandError("Agent environment or dataset validation failed.") from None
        model = os.getenv("OPENAI_MODEL", "gpt-6-sol").strip() or "gpt-6-sol"
        safe_model = model if re.fullmatch(r"gpt-[A-Za-z0-9._-]{1,64}", model) else "custom"
        key_configured = bool(os.getenv("OPENAI_API_KEY", "").strip())
        enabled = bool(getattr(settings, "REDORDA_RUN_EXECUTION_ENABLED", False))
        self.stdout.write(self.style.SUCCESS("Agent preflight: OK"))
        self.stdout.write(f"Customers: {customers}; excluded from base cells: {excluded}")
        self.stdout.write(f"Execution enabled: {str(enabled).lower()}")
        self.stdout.write(
            f"OpenAI key configured: {str(key_configured).lower()}; model: {safe_model}"
        )
        self.stdout.write("Pilots: 0; model requests: 0. Worker and broker are checked separately.")
