"""Transactional state changes for a single campaign calculation."""

import hashlib
import uuid

from django.db import transaction
from django.utils import timezone

from apps.campaigns.models import CampaignRun, RunEvent

TERMINAL = {"completed", "failed", "cancelled"}


class ExecutionConflict(Exception):
    """The requested transition is incompatible with the persisted run."""


class ExecutionUnavailable(Exception):
    """The calculation could not be queued or its engine is unavailable."""


class EngineNotReady(Exception):
    """A new calculation cannot start until the public agent is connected."""


def _task_id(run_id, key):
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return str(uuid.uuid5(uuid.UUID(str(run_id)), digest))


def _event(run, kind, payload=None):
    return RunEvent.objects.create(run=run, kind=kind, payload=payload or {})


def start_run(run_id, *, idempotency_key, execution_available=True):
    """Queue a draft once; matching retries return the same run."""
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ExecutionConflict("Idempotency-Key is required")
    if len(idempotency_key) > 255:
        raise ExecutionConflict("Idempotency-Key is too long")
    task_id = _task_id(run_id, idempotency_key)
    with transaction.atomic():
        run = CampaignRun.objects.select_for_update().get(pk=run_id)
        if run.status != CampaignRun.Status.DRAFT:
            if run.task_id == task_id:
                return run
            raise ExecutionConflict("Calculation has already been started")
        if not execution_available:
            raise EngineNotReady()
        run.status = CampaignRun.Status.QUEUED
        run.task_id = task_id
        run.cancel_requested = False
        run.save(update_fields=["status", "task_id", "cancel_requested"])
        _event(run, "queued", {"task_id": task_id})
        transaction.on_commit(lambda: _publish(run.pk, task_id))
    return run


def _publish(run_id, task_id):
    from apps.campaigns.tasks import RUN_TIMEOUT_SECONDS, execute_run, expire_run

    try:
        expire_run.apply_async(args=[str(run_id), task_id], countdown=RUN_TIMEOUT_SECONDS + 10,
                               retry=False)
        execute_run.apply_async(args=[str(run_id)], task_id=task_id, retry=False)
    except Exception as exc:
        # A transport exception is not proof that the broker did not accept the message.
        # Keep the task id and terminal state so a late delivery cannot run twice.
        finish_run(run_id, task_id, "failed", "queue_unavailable", str(exc))
        raise ExecutionUnavailable("Calculation queue is unavailable") from exc


def request_cancel(run_id):
    with transaction.atomic():
        run = CampaignRun.objects.select_for_update().get(pk=run_id)
        if run.status in TERMINAL:
            return run
        if run.status == CampaignRun.Status.DRAFT:
            raise ExecutionConflict("Calculation has not been started")
        if not run.cancel_requested:
            run.cancel_requested = True
            run.save(update_fields=["cancel_requested"])
            _event(run, "cancel_requested")
        if run.status == CampaignRun.Status.QUEUED:
            run.status = CampaignRun.Status.CANCELLED
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
            _event(run, "cancelled")
        return run


def begin_run(run_id, task_id):
    """Claim a queued delivery. Another delivery must never enter the engine."""
    with transaction.atomic():
        run = CampaignRun.objects.select_for_update().get(pk=run_id)
        if run.task_id != task_id or run.status != CampaignRun.Status.QUEUED:
            return None
        if run.cancel_requested:
            run.status = CampaignRun.Status.CANCELLED
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
            _event(run, "cancelled")
            return None
        run.status = CampaignRun.Status.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at"])
        _event(run, "running")
        return run


def cancellation_requested(run_id, task_id):
    run = CampaignRun.objects.only("status", "task_id", "cancel_requested").get(pk=run_id)
    return (run.task_id != task_id or run.cancel_requested
            or run.status != CampaignRun.Status.RUNNING)


def finish_run(run_id, task_id, status, error_code="", error_message=""):
    if status not in TERMINAL:
        raise ValueError("Expected a terminal status")
    with transaction.atomic():
        run = CampaignRun.objects.select_for_update().get(pk=run_id)
        if run.task_id != task_id or run.status in TERMINAL:
            return run
        if run.cancel_requested and status == "completed":
            status = "cancelled"
        run.status = status
        run.finished_at = timezone.now()
        run.error_code = error_code
        run.error_message = error_message[:2000]
        run.save(update_fields=["status", "finished_at", "error_code", "error_message"])
        _event(run, status, {"error_code": error_code} if error_code else {})
        return run
