import uuid

from django.db import models


class Dataset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    checksum = models.CharField(max_length=64, unique=True)
    source_dir = models.TextField()
    customer_count = models.PositiveIntegerField()
    summary = models.JSONField(default=dict)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]


class Subscriber(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="subscribers")
    id_number = models.TextField()
    profile = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "id_number"], name="unique_subscriber_per_dataset"
            ),
        ]


class CampaignRun(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Выполняется"
        COMPLETED = "completed", "Завершён"
        FAILED = "failed", "Ошибка"
        CANCELLED = "cancelled", "Отменён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, related_name="runs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    budget = models.DecimalField(max_digits=10, decimal_places=2, default=100_000)
    max_contacts = models.PositiveIntegerField(default=15_000)
    max_pilots = models.PositiveSmallIntegerField(default=20)
    seed = models.PositiveIntegerField(default=42)
    strategy = models.CharField(max_length=16, default="baseline")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
