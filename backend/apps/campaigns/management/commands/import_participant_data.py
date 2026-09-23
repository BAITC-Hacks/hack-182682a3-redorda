from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.campaigns.models import Dataset
from apps.campaigns.services.datasets import DatasetValidationError, validate_dataset


class Command(BaseCommand):
    help = "Validate public participant CSVs and save an idempotent dataset summary."

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, type=Path)

    def handle(self, *args, **options):
        source = options["path"].resolve()
        try:
            checksum, customer_count, summary = validate_dataset(source)
        except DatasetValidationError as exc:
            raise CommandError(str(exc)) from exc
        with transaction.atomic():
            dataset, created = Dataset.objects.get_or_create(checksum=checksum, defaults={
                "name": "Beeline · пакет участника",
                "source_dir": str(source),
                "customer_count": customer_count,
                "summary": summary,
            })
        self.stdout.write(self.style.SUCCESS(
            f"{'Imported' if created else 'Already imported'} {dataset.id}: "
            f"{customer_count} customers"
        ))
