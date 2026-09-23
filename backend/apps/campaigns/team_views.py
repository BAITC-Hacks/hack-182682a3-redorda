from decimal import Decimal
from importlib import import_module
from uuid import UUID

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CampaignRun
from .serializers import ErrorSerializer
from .team_serializers import (
    TeamCommandRequestSerializer,
    TeamCommandResponseSerializer,
    TeamSnapshotSerializer,
)


class TeamServiceUnavailable(APIException):
    status_code = 503
    default_detail = "Сервис команды сейчас недоступен."
    default_code = "team_unavailable"


class TeamConflict(APIException):
    status_code = 409
    default_detail = "Команда недоступна для выбранного snapshot или ключа."
    default_code = "team_conflict"


PRIVATE_KEYS = {
    "engine_state",
    "api_key",
    "idempotency_key",
    "password",
    "secret",
    "source_dir",
    "stack",
    "token",
    "traceback",
    "path",
    "file_path",
    "dataset_path",
    "internal_state",
    "private_effects",
    "trace",
}


def _public(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {
            key: _public(item)
            for key, item in value.items()
            if str(key).lower() not in PRIVATE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    return value


def _service(name):
    try:
        return import_module(f"apps.campaigns.services.{name}")
    except ImportError as exc:
        raise TeamServiceUnavailable() from exc


def _call(function, **kwargs):
    try:
        return function(**kwargs)
    except APIException:
        raise
    except LookupError as exc:
        raise NotFound("Команда или snapshot не найден.") from exc
    except ValueError as exc:
        raise TeamConflict() from exc
    except Exception as exc:
        raise TeamServiceUnavailable() from exc


def _run(pk):
    return get_object_or_404(CampaignRun, pk=pk)


def _same_run(payload, run_id):
    if "run_id" in payload:
        try:
            if UUID(str(payload["run_id"])) != run_id:
                raise NotFound("Команда не найдена.")
        except (TypeError, ValueError) as exc:
            raise TeamServiceUnavailable() from exc


def _project(payload, fields):
    return _public({key: payload[key] for key in fields if key in payload})


class TeamView(APIView):
    @extend_schema(
        responses={200: TeamSnapshotSerializer, 404: ErrorSerializer, 503: ErrorSerializer},
        tags=["team"],
    )
    def get(self, request, id):
        run = _run(id)
        payload = _call(_service("team_state").get_team_snapshot, run_id=run.id)
        if not isinstance(payload, dict):
            raise TeamServiceUnavailable()
        _same_run(payload, run.id)
        return Response(_project(payload, ("schema_version", "run_id", "snapshot_id",
                                           "last_event_id", "tasks", "artifacts",
                                           "available_commands")))


class CommandListView(APIView):
    @extend_schema(
        request=TeamCommandRequestSerializer,
        parameters=[
            OpenApiParameter(
                "Idempotency-Key",
                str,
                OpenApiParameter.HEADER,
                required=True,
                description="Ключ длиной 1–255 символов.",
            )
        ],
        responses={
            202: TeamCommandResponseSerializer,
            400: ErrorSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
            503: ErrorSerializer,
        },
        tags=["team"],
    )
    def post(self, request, id):
        run = _run(id)
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 255:
            raise serializers.ValidationError(
                {"Idempotency-Key": ["Укажите ключ длиной 1–255 символов."]}
            )
        body = TeamCommandRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        state = _service("team_state")
        _call(state.load_snapshot, run_id=run.id, snapshot_id=data["snapshot_id"])
        parameters = _public(data["parameters"])
        payload = _call(
            _service("team_commands").submit_command,
            run_id=run.id,
            command_type=data["type"],
            snapshot_id=data["snapshot_id"],
            parameters=parameters,
            idempotency_key=key,
        )
        if not isinstance(payload, dict):
            raise TeamServiceUnavailable()
        _same_run(payload, run.id)
        return Response(_project(payload, ("id", "type", "status", "result", "error")),
                        status=status.HTTP_202_ACCEPTED)


class CommandDetailView(APIView):
    @extend_schema(
        responses={200: TeamCommandResponseSerializer, 404: ErrorSerializer, 503: ErrorSerializer},
        tags=["team"],
    )
    def get(self, request, id, command_id):
        run = _run(id)
        payload = _call(_service("team_commands").get_command, run_id=run.id, command_id=command_id)
        if not isinstance(payload, dict):
            raise TeamServiceUnavailable()
        _same_run(payload, run.id)
        return Response(_project(payload, ("id", "type", "status", "result", "error")))
