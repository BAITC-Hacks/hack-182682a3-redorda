"""Durable projection of the team's observed work and saved campaign facts."""

import uuid

from django.db import transaction
from django.db.models import Max

from apps.campaigns.models import (
    CampaignRun,
    RunEvent,
    TeamArtifact,
    TeamSnapshot,
    TeamTask,
)

ROLES = {"lead", "analyst", "experiment", "finance", "control"}
ARTIFACT_TYPES = {"hypotheses", "pilot_observation", "portfolio", "explanation",
                  "comparison", "validation"}
TASK_EVENTS = {"task_started": "running", "task_completed": "completed",
               "task_failed": "failed"}
PRIVATE_KEYS = {"api_key", "password", "secret", "source_dir", "stack", "token",
                "traceback", "path", "private_effects"}


def _public(value):
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if key not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _references(run_id, ids):
    if not isinstance(ids, list) or any(not isinstance(item, (str, int)) for item in ids):
        raise ValueError("Evidence IDs must be a list")
    for item in ids:
        if TeamArtifact.objects.filter(pk=str(item), run_id=run_id).exists():
            continue
        if str(item).isdigit() and RunEvent.objects.filter(pk=int(item), run_id=run_id).exists():
            continue
        raise ValueError(f"Evidence {item} does not belong to this run")


def _task(run_id, task_id, *, actor_id=None, title=None):
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise ValueError("Invalid task ID")
    task = TeamTask.objects.filter(run_id=run_id, task_id=task_id).first()
    if task is None:
        if actor_id not in ROLES or not title:
            raise ValueError("A new task requires actor and title")
        task = TeamTask.objects.create(run_id=run_id, task_id=task_id, actor_id=actor_id,
                                       title=str(title)[:255])
    elif actor_id is not None and task.actor_id != actor_id:
        raise ValueError("Task actor cannot change")
    return task


def _artifact_dict(artifact):
    return {"id": artifact.id, "task_id": artifact.task.task_id, "type": artifact.type,
            "title": artifact.title, "data": artifact.data, "evidence_ids": artifact.evidence_ids}


def publish_artifact(*, run_id, artifact):
    """Save once. A reused ID must have identical content and belong to this run."""
    if not isinstance(artifact, dict):
        raise ValueError("Artifact must be an object")
    artifact_id = str(artifact.get("id") or uuid.uuid4())
    if len(artifact_id) > 128:
        raise ValueError("Invalid artifact ID")
    with transaction.atomic():
        CampaignRun.objects.select_for_update().get(pk=run_id)
        existing = TeamArtifact.objects.filter(pk=artifact_id).select_related("task").first()
        if existing:
            if str(existing.run_id) != str(run_id):
                raise ValueError("Artifact belongs to another run")
            if _artifact_dict(existing) != {**artifact, "id": artifact_id}:
                raise ValueError("Artifact IDs are immutable")
            return _artifact_dict(existing)
        kind = artifact.get("type")
        title = artifact.get("title")
        data = artifact.get("data")
        if kind not in ARTIFACT_TYPES or not isinstance(title, str) or not title:
            raise ValueError("Invalid artifact type or title")
        if not isinstance(data, dict):
            raise ValueError("Artifact data must be an object")
        evidence = artifact.get("evidence_ids", [])
        _references(run_id, evidence)
        task = _task(run_id, artifact.get("task_id"))
        saved = TeamArtifact.objects.create(id=artifact_id, run_id=run_id, task=task,
                                            type=kind, title=title, data=data,
                                            evidence_ids=evidence)
        return _artifact_dict(saved)


def persist_team_event(*, run_id, kind, payload):
    """Persist links and their event in one transaction, serialized on the run row."""
    if kind not in (*TASK_EVENTS, "task_handoff") or not isinstance(payload, dict):
        raise ValueError("Unsupported team event")
    with transaction.atomic():
        CampaignRun.objects.select_for_update().get(pk=run_id)
        data = dict(payload)
        artifacts = data.pop("artifacts", [])
        if not isinstance(artifacts, list):
            raise ValueError("Artifacts must be a list")
        task = _task(run_id, data.get("task_id"), actor_id=data.get("actor_id"),
                     title=data.get("title"))
        evidence = data.get("evidence_ids", [])
        _references(run_id, evidence)
        ids = list(data.get("artifact_ids", []))
        if not isinstance(ids, list):
            raise ValueError("Artifact IDs must be a list")
        for artifact in artifacts:
            if artifact.get("task_id") != task.task_id:
                raise ValueError("Artifact and task must match")
            saved = publish_artifact(run_id=run_id, artifact=artifact)
            if saved["id"] not in ids:
                ids.append(saved["id"])
        for artifact_id in ids:
            if not TeamArtifact.objects.filter(pk=str(artifact_id), run_id=run_id).exists():
                raise ValueError("Artifact does not belong to this run")
        data["artifact_ids"] = ids
        data["evidence_ids"] = evidence
        if kind in TASK_EVENTS:
            status = TASK_EVENTS[kind]
            if data.get("status", status) != status:
                raise ValueError("Task event status mismatch")
            task.status = status
            task.title = str(data.get("title") or task.title)[:255]
            task.artifact_ids = list(dict.fromkeys([*task.artifact_ids, *ids]))
            task.evidence_ids = list(dict.fromkeys([*task.evidence_ids, *evidence]))
            task.save(update_fields=["status", "title", "artifact_ids", "evidence_ids",
                                     "updated_at"])
            data["status"] = status
        else:
            if data.get("from_actor") != task.actor_id or data.get("to_actor") not in ROLES:
                raise ValueError("Invalid task handoff actors")
            to_task = _task(run_id, data.get("to_task_id"), actor_id=data["to_actor"],
                            title=data.get("to_title") or data.get("title"))
            if to_task.pk == task.pk:
                raise ValueError("Task cannot hand off to itself")
        return RunEvent.objects.create(run_id=run_id, kind=kind, payload=data)


def get_team_snapshot(*, run_id) -> dict:
    with transaction.atomic():
        run = CampaignRun.objects.select_for_update().select_related("dataset").get(pk=run_id)
        cursor = RunEvent.objects.filter(run=run).aggregate(last=Max("id"))["last"] or 0
        tasks = [{"id": task.task_id, "actor_id": task.actor_id,
                  "status": "cancelled" if run.status == "cancelled" and task.status == "running"
                  else task.status, "title": task.title,
                  "artifact_ids": task.artifact_ids, "evidence_ids": task.evidence_ids}
                 for task in run.team_tasks.order_by("created_at", "pk")]
        artifacts = [_public(_artifact_dict(item)) for item in
                     run.team_artifacts.select_related("task").order_by("created_at", "pk")]
        commands = []
        if run.status == "completed" and run.campaign_results.exists():
            commands = ["explain", "compare", "create_plan"]
        public = {"schema_version": 1, "run_id": str(run.pk), "last_event_id": cursor,
                  "tasks": tasks, "artifacts": artifacts, "available_commands": commands}
        summary = run.result.summary if hasattr(run, "result") else None
        engine = {"schema_version": 1, "engine_version": (
            summary.get("metadata", {}).get("engine_version") if summary else None),
            "dataset_checksum": run.dataset.checksum,
            "config": {"budget": str(run.budget), "max_contacts": run.max_contacts,
                       "max_pilots": run.max_pilots, "seed": run.seed,
                       "strategy": run.strategy, "constraints": run.constraints},
            "pilots": [{"sequence": pilot.sequence, "request": pilot.request,
                        "response": pilot.response, "cost": str(pilot.cost),
                        "n_customers": pilot.n_customers}
                       for pilot in run.pilots.order_by("sequence")],
            "campaigns": [{"id": result.rank, "campaign": result.campaign,
                           "metrics": result.metrics} for result in
                          run.campaign_results.order_by("rank")],
            "resource_usage": summary.get("resource_usage") if summary else None,
            "estimates": summary.get("estimates") if summary else None}
        snapshot = TeamSnapshot.objects.create(run=run, schema_version=1,
                                               last_event_id=cursor, public_state=public,
                                               engine_state=engine)
        return {**public, "snapshot_id": str(snapshot.pk)}


def load_snapshot(*, run_id, snapshot_id) -> TeamSnapshot:
    return TeamSnapshot.objects.get(pk=snapshot_id, run_id=run_id)
