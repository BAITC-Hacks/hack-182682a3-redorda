from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.campaigns.models import Dataset, Subscriber
from apps.campaigns.services.datasets import (
    REQUIRED_COLUMNS,
    DatasetValidationError,
    read_csv,
    validate_dataset,
)


class Command(BaseCommand):
    help = "Validate public participant CSVs and save the dataset summary and subscriber profiles."

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
            dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
            if not dataset.subscribers.exists():
                Subscriber.objects.bulk_create((
                    Subscriber(dataset=dataset, id_number=row["ID_NUMBER"].strip(), profile=row)
                    for _, row in read_csv(source / "customer_profile.csv",
                                           REQUIRED_COLUMNS["customer_profile.csv"])
                ), batch_size=500)
        self.stdout.write(self.style.SUCCESS(
            f"{'Imported' if created else 'Already imported'} {dataset.id}: "
            f"{customer_count} customers"
        ))
