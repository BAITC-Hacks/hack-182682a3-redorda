import csv
import hashlib
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.campaigns.models import Dataset, Subscriber


class Command(BaseCommand):
    help = "Validate public participant CSVs and save the dataset summary and subscriber profiles."

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, type=Path)

    @transaction.atomic
    def handle(self, *args, **options):
        source = options["path"].resolve()
        profiles = source / "customer_profile.csv"
        tariffs = source / "data" / "dict_tariff.csv"
        if not profiles.is_file() or not tariffs.is_file():
            raise CommandError("Expected customer_profile.csv and data/dict_tariff.csv")
        tariff_rows = list(csv.DictReader(tariffs.open(encoding="utf-8-sig", newline="")))
        if not tariff_rows or "tariff_plan_code" not in tariff_rows[0]:
            raise CommandError("Tariff dictionary is missing tariff_plan_code")
        tariff_ids = {row["tariff_plan_code"] for row in tariff_rows}
        segments = {name: Counter() for name in ["arpu_segment", "data_segment", "call_segment"]}
        allowed = {"arpu_segment": {"LOW", "MID", "HIGH"},
                   "data_segment": {"NON_USER", "LITE", "HEAVY"},
                   "call_segment": {"LOW", "MEDIUM", "HIGH"}}
        ids, profiles_data, total_arpu = set(), [], Decimal("0")
        missing_current_tariff = 0
        with profiles.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"ID_NUMBER", "predicted_arpu", "current_tariff", *segments}
            if not required.issubset(reader.fieldnames or []):
                raise CommandError("Profile CSV is missing required columns")
            for row_number, row in enumerate(reader, 2):
                customer_id = row["ID_NUMBER"]
                if not customer_id or customer_id in ids:
                    raise CommandError(f"Duplicate or missing ID_NUMBER at row {row_number}")
                if row["current_tariff"] and row["current_tariff"] not in tariff_ids:
                    raise CommandError(f"Unknown tariff at row {row_number}")
                if not row["current_tariff"]:
                    missing_current_tariff += 1
                try:
                    value = Decimal(row["predicted_arpu"])
                    if not value.is_finite() or value < 0:
                        raise InvalidOperation
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise CommandError(f"Invalid predicted_arpu at row {row_number}") from exc
                ids.add(customer_id)
                profiles_data.append(row.copy())
                total_arpu += value
                for name, counts in segments.items():
                    if row[name] and row[name] not in allowed[name]:
                        raise CommandError(f"Invalid {name} at row {row_number}")
                    counts[row[name] or "UNKNOWN"] += 1
        if not ids:
            raise CommandError("Profile dataset is empty")
        checksum = hashlib.sha256(profiles.read_bytes() + tariffs.read_bytes()).hexdigest()
        dataset, created = Dataset.objects.update_or_create(checksum=checksum, defaults={
            "name": "Beeline · пакет участника", "source_dir": str(source),
            "customer_count": len(ids), "summary": {
                "baseline_arpu": str(total_arpu.quantize(Decimal("0.01"))),
                "tariff_count": len(tariff_ids), "segments": segments,
                "missing_current_tariff": missing_current_tariff,
                "synthetic": True,
            },
        })
        dataset.subscribers.all().delete()
        for start in range(0, len(profiles_data), 500):
            Subscriber.objects.bulk_create([
                Subscriber(dataset=dataset, id_number=row["ID_NUMBER"], profile=row)
                for row in profiles_data[start:start + 500]
            ], batch_size=500)
        self.stdout.write(self.style.SUCCESS(
            f"{'Imported' if created else 'Updated'} {dataset.id}: {len(ids)} customers"
        ))
