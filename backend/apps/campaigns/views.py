from decimal import Decimal
from itertools import chain
from uuid import UUID

from campaign_engine.contracts import CASE_LIMITS, CHANNEL_COSTS
from config.runtime import execution_enabled, openai_enabled, strategy_enabled
from django.apps import apps
from django.db import connection
from django.http import StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CampaignRun, Dataset
from .serializers import (
    DatasetSerializer,
    ErrorSerializer,
    HealthSerializer,
    MetaSerializer,
    RunEventPageSerializer,
    RunResultSerializer,
    RunSerializer,
)


class DatasetRequired(APIException):
    status_code = 409
    default_detail = "Сначала импортируйте пакет данных Beeline."
    default_code = "dataset_required"


class RunConflict(APIException):
    status_code = 409
    default_detail = "Операция недоступна в текущем состоянии плана."
    default_code = "run_conflict"


class ServiceUnavailable(APIException):
    status_code = 503
    default_detail = "Сервис расчёта сейчас недоступен."
    default_code = "execution_unavailable"


class EngineUnavailable(APIException):
    status_code = 503
    default_detail = "Агент ещё не подключён. Запуск расчётов пока недоступен."
    default_code = "engine_unavailable"


class OpenAIUnavailable(APIException):
    status_code = 503
    default_detail = "Режим OpenAI сейчас недоступен на сервере."
    default_code = "openai_unavailable"


class ResultNotAvailable(APIException):
    status_code = 409
    default_detail = "Результат ещё не готов."
    default_code = "result_not_ready"


class InvalidResult(APIException):
    status_code = 500
    default_detail = "Сохранённый результат недоступен."
    default_code = "invalid_result"


def _json_safe(value):
    """Keep monetary/identifier values stable when a result service returns Python objects."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _public_payload(value):
    if isinstance(value, dict):
        private = {"api_key", "idempotency_key", "password", "secret", "source_dir",
                   "stack", "task_id", "token", "traceback", "path"}
        return {key: _public_payload(item) for key, item in value.items()
                if str(key).lower() not in private}
    if isinstance(value, list):
        return [_public_payload(item) for item in value]
    return _json_safe(value)


class HealthView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=HealthSerializer)
    def get(self, request):
        connection.ensure_connection()
        return Response({"status": "ok", "service": "redorda-api"})


class MetaView(APIView):
    @extend_schema(responses=MetaSerializer)
    def get(self, request):
        return Response({
            "limits": CASE_LIMITS,
            "channel_costs": CHANNEL_COSTS,
            "features": {"run_execution": execution_enabled(), "openai_strategy": openai_enabled(),
                         "csv_export": True},
        })


class CurrentDatasetView(generics.RetrieveAPIView):
    serializer_class = DatasetSerializer

    def get_object(self):
        dataset = Dataset.objects.first()
        if dataset is None:
            raise NotFound("Набор данных ещё не импортирован.")
        return dataset


class RunViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                 viewsets.GenericViewSet):
    serializer_class = RunSerializer
    queryset = CampaignRun.objects.select_related("dataset").prefetch_related(
        "pilots", "campaign_results").all()
    lookup_value_regex = (
        "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )

    def perform_create(self, serializer):
        dataset = Dataset.objects.first()
        if dataset is None:
            raise DatasetRequired()
        serializer.save(dataset=dataset)

    @extend_schema(request=None, responses={202: RunSerializer, 400: ErrorSerializer,
                                            404: ErrorSerializer, 409: ErrorSerializer,
                                            503: ErrorSerializer}, parameters=[
        OpenApiParameter("Idempotency-Key", str, OpenApiParameter.HEADER, required=True,
                         description="Стабильный ключ запроса запуска, максимум 255 символов."),
    ])
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        run = self.get_object()
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 255:
            raise serializers.ValidationError({
                "Idempotency-Key": ["Укажите ключ длиной 1–255 символов."]
            })
        from .services import execution

        try:
            run = execution.start_run(run.id, idempotency_key=key,
                                      execution_available=strategy_enabled(run.strategy))
        except execution.ExecutionConflict as exc:
            raise RunConflict() from exc
        except execution.EngineNotReady as exc:
            if run.strategy == "openai" and not openai_enabled():
                raise OpenAIUnavailable() from exc
            raise EngineUnavailable() from exc
        except execution.ExecutionUnavailable as exc:
            raise ServiceUnavailable() from exc
        return Response(self.get_serializer(run).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(request=None, responses={200: RunSerializer, 404: ErrorSerializer,
                                            409: ErrorSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        run = self.get_object()
        from .services import execution

        try:
            run = execution.request_cancel(run.id)
        except execution.ExecutionConflict as exc:
            raise RunConflict() from exc
        return Response(self.get_serializer(run).data)

    @extend_schema(parameters=[
        OpenApiParameter("after", int, OpenApiParameter.QUERY,
                         description="ID последнего полученного события; от 0."),
        OpenApiParameter("limit", int, OpenApiParameter.QUERY,
                         description="Размер страницы от 1 до 200."),
    ], responses={200: RunEventPageSerializer, 400: ErrorSerializer, 404: ErrorSerializer})
    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        run = self.get_object()
        params = EventQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        after = params.validated_data["after"]
        limit = params.validated_data["limit"]
        if after > 2**63 - 1:
            return Response({"results": [], "next_after": after, "has_more": False})
        run_event = apps.get_model("campaigns", "RunEvent")
        page = list(run_event.objects.filter(run=run, id__gt=after).order_by("id")[:limit + 1])
        has_more = len(page) > limit
        page = page[:limit]
        results = [{"id": event.id, "kind": event.kind, "payload": _public_payload(event.payload),
                    "created_at": event.created_at} for event in page]
        encoded = RunEventPageSerializer().fields["results"].to_representation(results)
        return Response({"results": encoded, "next_after": page[-1].id if page else after,
                         "has_more": has_more})

    @extend_schema(responses={200: RunResultSerializer, 404: ErrorSerializer,
                              409: ErrorSerializer, 500: ErrorSerializer})
    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        run = self.get_object()
        from .services import results

        try:
            result = results.get_results(run.id)
        except results.ResultNotReady as exc:
            raise ResultNotAvailable() from exc
        except results.InvalidSavedResult as exc:
            raise InvalidResult() from exc
        return Response(_json_safe(result))

    @extend_schema(responses={(200, "text/csv"): OpenApiTypes.BINARY, 404: ErrorSerializer,
                              409: ErrorSerializer, 500: ErrorSerializer})
    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        run = self.get_object()
        if run.status != CampaignRun.Status.COMPLETED:
            raise ResultNotAvailable()
        from .services import results

        try:
            rows = iter(results.iter_submission_csv(run.id))
            first_row = next(rows)
        except results.ResultNotReady as exc:
            raise ResultNotAvailable() from exc
        except results.InvalidSavedResult as exc:
            raise InvalidResult() from exc
        except StopIteration as exc:
            raise InvalidResult() from exc
        response = StreamingHttpResponse(chain([first_row], rows),
                                         content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="redorda-{run.id}.csv"'
        return response


class EventQuerySerializer(serializers.Serializer):
    after = serializers.IntegerField(min_value=0, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=200, default=100)
