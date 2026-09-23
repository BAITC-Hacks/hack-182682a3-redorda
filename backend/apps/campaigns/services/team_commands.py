"""Durable, idempotent commands against immutable team snapshots."""

import json
import logging
from decimal import Decimal, InvalidOperation

from campaign_engine.contracts import CHANNEL_COSTS
from django.db import transaction
from django.http import Http404
from rest_framework.exceptions import APIException, ValidationError

from apps.campaigns.models import CampaignRun
from apps.campaigns.services import team_ai

logger = logging.getLogger(__name__)
COMMAND_TYPES = {"explain", "compare", "create_plan"}


class CommandConflict(APIException):
    status_code = 409
    default_code = "command_conflict"
    default_detail = "Idempotency-Key уже использован для другого запроса."


def _command_model():
    from apps.campaigns.models import TeamCommand

    return TeamCommand


def _type_field(model):
    names = {field.name for field in model._meta.fields}
    return "command_type" if "command_type" in names else "type"


def _error(code, message):
    return {"code": code, "message": message, "fields": {}}


def _public(command):
    return {"id": str(command.pk), "type": getattr(command, _type_field(type(command))),
            "status": command.status, "result": command.result, "error": command.error}


def _constraints(value):
    if not isinstance(value, dict):
        raise ValidationError({"constraints": ["Ожидается объект ограничений."]})
    unknown = set(value) - {"budget", "allowed_channels"}
    if unknown:
        raise ValidationError({"constraints": [f"Неизвестные поля: {', '.join(sorted(unknown))}."]})
    if not value:
        raise ValidationError({"constraints": ["Укажите хотя бы одно ограничение."]})
    result = {}
    if "budget" in value:
        raw = value["budget"]
        if not isinstance(raw, str):
            raise ValidationError({"constraints": {"budget": ["Ожидается decimal-строка."]}})
        try:
            budget = Decimal(raw)
        except InvalidOperation as exc:
            raise ValidationError({"constraints": {"budget": ["Некорректный бюджет."]}}) from exc
        if not budget.is_finite() or not 0 < budget <= 100_000 or budget.as_tuple().exponent < -2:
            raise ValidationError({"constraints": {"budget": [
                "Бюджет должен быть от 0.01 до 100000.00."]}})
        result["budget"] = f"{budget:.2f}"
    if "allowed_channels" in value:
        channels = value["allowed_channels"]
        if (not isinstance(channels, list) or not channels
                or any(not isinstance(item, str) or item not in CHANNEL_COSTS for item in channels)
                or len(channels) != len(set(channels))):
            raise ValidationError({"constraints": {"allowed_channels": [
                "Укажите непустой список уникальных допустимых каналов."]}})
        result["allowed_channels"] = channels
    return result


def _parameters(command_type, parameters):
    if command_type not in COMMAND_TYPES:
        raise ValidationError({"type": ["Неизвестная команда."]})
    if not isinstance(parameters, dict):
        raise ValidationError({"parameters": ["Ожидается объект."]})
    allowed = {"explain": {"campaign_id"}, "compare": {"constraints"},
               "create_plan": {"name", "constraints"}}[command_type]
    unknown = set(parameters) - allowed
    if unknown:
        raise ValidationError({"parameters": [f"Неизвестные поля: {', '.join(sorted(unknown))}."]})
    if command_type == "explain":
        campaign_id = parameters.get("campaign_id")
        if not isinstance(campaign_id, str) or not campaign_id.strip():
            raise ValidationError({"campaign_id": ["Укажите ID кампании."]})
        return parameters
    result = {"constraints": _constraints(parameters.get("constraints"))}
    if command_type == "create_plan":
        name = parameters.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValidationError({"name": ["Укажите название длиной до 120 символов."]})
        result["name"] = name.strip()
    return parameters


def submit_command(*, run_id, command_type, snapshot_id, parameters, idempotency_key) -> dict:
    from apps.campaigns.services import team_state

    if not isinstance(idempotency_key, str) or not 0 < len(idempotency_key.strip()) <= 255:
        raise ValidationError({"Idempotency-Key": ["Укажите ключ длиной 1–255 символов."]})
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValidationError({"snapshot_id": ["Укажите ID снимка."]})
    key = idempotency_key.strip()
    validated = _parameters(command_type, parameters)
    model = _command_model()
    type_field = _type_field(model)
    with transaction.atomic():
        run = CampaignRun.objects.select_for_update().get(pk=run_id)
        previous = model.objects.filter(run=run, idempotency_key=key).first()
        if previous is not None:
            same = (getattr(previous, type_field) == command_type
                    and str(previous.snapshot_id) == snapshot_id
                    and previous.parameters == validated)
            if not same:
                raise CommandConflict()
            return _public(previous)
        snapshot = team_state.load_snapshot(run_id=run_id, snapshot_id=snapshot_id)
        if snapshot.schema_version != 1 or not isinstance(snapshot.engine_state, dict):
            raise ValidationError({"snapshot_id": ["Неподдерживаемая версия снимка."]})
        payload = {"run": run, "snapshot_id": snapshot.pk, type_field: command_type,
                   "parameters": validated, "idempotency_key": key,
                   "status": "queued", "result": None, "error": None}
        command = model.objects.create(**payload)
        transaction.on_commit(lambda: _publish(command.pk))
    command.refresh_from_db()
    return _public(command)


def get_command(*, run_id, command_id) -> dict:
    command = _command_model().objects.filter(run_id=run_id, pk=command_id).first()
    if command is None:
        raise Http404("Команда не найдена.")
    return _public(command)


def _publish(command_id):
    from apps.campaigns.tasks import execute_team_command, expire_team_command

    try:
        expire_team_command.apply_async(args=[str(command_id)], countdown=610, retry=False)
        execute_team_command.apply_async(args=[str(command_id)], task_id=str(command_id),
                                         retry=False)
    except Exception:
        logger.exception("Could not queue team command %s", command_id)
        _finish(command_id, error=_error("queue_unavailable", "Очередь команд недоступна."))


def _claim(command_id):
    model = _command_model()
    with transaction.atomic():
        command = model.objects.select_for_update().get(pk=command_id)
        if command.status != "queued":
            return None
        command.status = "running"
        command.save(update_fields=["status"])
        return command


def _finish(command_id, *, result=None, error=None):
    model = _command_model()
    with transaction.atomic():
        command = model.objects.select_for_update().get(pk=command_id)
        if command.status in {"completed", "failed"}:
            return command
        command.status = "failed" if error else "completed"
        command.result = result
        command.error = error
        command.save(update_fields=["status", "result", "error"])
        return command


def _artifact_result(command, kind, data):
    from apps.campaigns.services import team_state

    if not isinstance(data, dict) or data.get("type") != kind:
        raise TypeError("AI returned an invalid artifact")
    if not isinstance(data.get("evidence_ids"), list):
        raise TypeError("AI artifact has no evidence list")
    saved = team_state.publish_artifact(run_id=command.run_id, artifact=data)
    if not isinstance(saved, dict) or not saved.get("id"):
        raise TypeError("Artifact could not be saved")
    return saved


def _create_plan(command):
    source = command.run
    field_names = {field.name for field in CampaignRun._meta.fields}
    if not {"parent_run", "constraints"} <= field_names:
        raise team_ai.CapabilityUnavailable("Linked plan storage is unavailable")
    constraints = {**(source.constraints or {}), **command.parameters["constraints"]}
    values = {name: getattr(source, name) for name in
              ("dataset", "budget", "max_contacts", "max_pilots", "seed", "strategy")}
    values.update(name=command.parameters["name"], parent_run=source,
                  constraints=constraints)
    if "budget" in constraints:
        values["budget"] = Decimal(constraints["budget"])
    with transaction.atomic():
        locked = _command_model().objects.select_for_update().get(pk=command.pk)
        if locked.status != "running":
            return None
        plan = CampaignRun.objects.create(**values)
        locked.status = "completed"
        locked.result = {"run_id": str(plan.pk)}
        locked.save(update_fields=["status", "result"])
        return locked.result


def execute_command(command_id):
    from apps.campaigns.services import team_state

    command = _claim(command_id)
    if command is None:
        return
    command_type = None
    try:
        command_type = getattr(command, _type_field(type(command)))
        if command_type == "create_plan":
            result = _create_plan(command)
        else:
            snapshot = team_state.load_snapshot(run_id=command.run_id,
                                                snapshot_id=str(command.snapshot_id))
            state = snapshot.engine_state
            if command_type == "explain":
                artifact = team_ai.explain(state, command.parameters["campaign_id"])
                result = _artifact_result(command, "explanation", artifact)
            elif command_type == "compare":
                artifact = team_ai.compare(state, command.parameters["constraints"])
                result = _artifact_result(command, "comparison", artifact)
            else:
                raise ValueError("Unsupported command type")
    except team_ai.CapabilityUnavailable:
        _finish(command_id, error=_error("capability_unavailable", "AI-функция пока недоступна."))
    except ValueError:
        logger.exception("Team command %s rejected its input", command_id)
        if command_type == "compare":
            _finish(command_id, error=_error("invalid_constraints", "Ограничения невыполнимы."))
        else:
            _finish(command_id, error=_error("command_failed", "Команда завершилась с ошибкой."))
    except Exception:
        logger.exception("Team command %s failed", command_id)
        _finish(command_id, error=_error("command_failed", "Команда завершилась с ошибкой."))
    else:
        _finish(command_id, result=json.loads(json.dumps(result, default=str)))


def expire_command(command_id):
    model = _command_model()
    if model.objects.filter(pk=command_id, status__in=["queued", "running"]).exists():
        _finish(command_id, error=_error("timeout", "Команда превысила время выполнения."))
