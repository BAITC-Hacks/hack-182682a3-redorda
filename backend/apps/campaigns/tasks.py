"""Celery calculation and a one-shot timeout guard."""

import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from apps.campaigns.models import CampaignRun
from apps.campaigns.services.engine_bridge import ExecutionCancelled, run_engine
from apps.campaigns.services.execution import (
    ExecutionUnavailable,
    begin_run,
    cancellation_requested,
    finish_run,
)

logger = logging.getLogger(__name__)
RUN_TIMEOUT_SECONDS = 600


@shared_task(bind=True, max_retries=0, acks_late=False, soft_time_limit=570, time_limit=600)
def execute_run(self, run_id):
    task_id = self.request.id
    run = begin_run(run_id, task_id)
    if run is None:
        return
    deadline = timezone.now() + timedelta(seconds=RUN_TIMEOUT_SECONDS - 30)

    def check_cancel():
        if timezone.now() >= deadline:
            raise TimeoutError("Calculation exceeded its deadline")
        return cancellation_requested(run_id, task_id)

    try:
        run_engine(run, check_cancel=check_cancel)
    except ExecutionCancelled:
        finish_run(run_id, task_id, "cancelled")
    except (TimeoutError, SoftTimeLimitExceeded) as exc:
        finish_run(run_id, task_id, "failed", "timeout", str(exc))
    except ExecutionUnavailable as exc:
        finish_run(run_id, task_id, "failed", "engine_unavailable", str(exc))
    except Exception as exc:
        logger.exception("Campaign calculation failed for run %s", run_id)
        finish_run(run_id, task_id, "failed", "execution_failed", str(exc))
    else:
        finish_run(run_id, task_id, "completed")


@shared_task(max_retries=0)
def expire_run(run_id, task_id):
    """Finalize a run if a worker was killed before handling its soft limit."""
    run = CampaignRun.objects.only("status", "started_at", "created_at").get(pk=run_id)
    if run.status in (CampaignRun.Status.QUEUED, CampaignRun.Status.RUNNING):
        finish_run(run_id, task_id, "failed", "timeout", "Calculation exceeded 600 seconds")
