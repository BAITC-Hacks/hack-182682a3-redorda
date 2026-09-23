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
    parent_run = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="derived_runs")
    constraints = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    budget = models.DecimalField(max_digits=10, decimal_places=2, default=100_000)
    max_contacts = models.PositiveIntegerField(default=15_000)
    max_pilots = models.PositiveSmallIntegerField(default=20)
    seed = models.PositiveIntegerField(default=42)
    strategy = models.CharField(max_length=16, default="baseline")
    created_at = models.DateTimeField(auto_now_add=True)
    task_id = models.CharField(max_length=255, blank=True, default="")
    cancel_requested = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]


class RunEvent(models.Model):
    run = models.ForeignKey(CampaignRun, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class Pilot(models.Model):
    run = models.ForeignKey(CampaignRun, on_delete=models.CASCADE, related_name="pilots")
    sequence = models.PositiveSmallIntegerField()
    request = models.JSONField(default=dict)
    response = models.JSONField(default=dict)
    cost = models.DecimalField(max_digits=12, decimal_places=2)
    n_customers = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["run", "sequence"], name="unique_pilot_run_sequence")
        ]


class CampaignResult(models.Model):
    run = models.ForeignKey(CampaignRun, on_delete=models.CASCADE, related_name="campaign_results")
    rank = models.PositiveSmallIntegerField()
    campaign = models.JSONField(default=dict)
    metrics = models.JSONField(default=dict)
    explanation = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(fields=["run", "rank"], name="unique_campaign_run_rank")
        ]


class RunResult(models.Model):
    run = models.OneToOneField(CampaignRun, on_delete=models.CASCADE, related_name="result")
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class TeamTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(CampaignRun, on_delete=models.CASCADE, related_name="team_tasks")
    task_id = models.CharField(max_length=128)
    actor_id = models.CharField(max_length=16)
    status = models.CharField(max_length=16, default="pending")
    title = models.CharField(max_length=255)
    artifact_ids = models.JSONField(default=list)
    evidence_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "task_id"],
                                               name="unique_team_task_run_id")]


class TeamArtifact(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    run = models.ForeignKey(CampaignRun, on_delete=models.CASCADE, related_name="team_artifacts")
    task = models.ForeignKey(TeamTask, on_delete=models.PROTECT, related_name="artifacts")
    type = models.CharField(max_length=32)
    title = models.CharField(max_length=255)
    data = models.JSONField(default=dict)
    evidence_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.task_id and self.run_id != self.task.run_id:
            raise ValueError("Artifact and task must belong to the same run")
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Team artifacts are immutable")
        return super().save(*args, **kwargs)


class TeamSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(CampaignRun, on_delete=models.CASCADE, related_name="team_snapshots")
    schema_version = models.PositiveSmallIntegerField(default=1)
    last_event_id = models.PositiveBigIntegerField(default=0)
    public_state = models.JSONField(default=dict)
    engine_state = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Team snapshots are immutable")
        return super().save(*args, **kwargs)


class TeamCommand(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(CampaignRun, on_delete=models.CASCADE, related_name="team_commands")
    snapshot = models.ForeignKey(TeamSnapshot, on_delete=models.PROTECT,
                                 related_name="commands")
    type = models.CharField(max_length=32)
    parameters = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default="queued")
    result = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.snapshot_id and self.run_id != self.snapshot.run_id:
            raise ValueError("Command and snapshot must belong to the same run")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "idempotency_key"],
                                               name="unique_team_command_run_key")]
