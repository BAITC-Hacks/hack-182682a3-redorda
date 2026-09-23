from campaign_engine.contracts import CASE_LIMITS, CHANNEL_COSTS
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import generics, mixins, viewsets
from rest_framework.exceptions import APIException, NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CampaignRun, Dataset
from .serializers import DatasetSerializer, HealthSerializer, MetaSerializer, RunSerializer


class DatasetRequired(APIException):
    status_code = 409
    default_detail = "Сначала импортируйте пакет данных Beeline."
    default_code = "dataset_required"


class HealthView(APIView):
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
            "features": {"run_execution": False, "openai_strategy": False, "csv_export": False},
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
    queryset = CampaignRun.objects.select_related("dataset").all()

    def perform_create(self, serializer):
        dataset = Dataset.objects.first()
        if dataset is None:
            raise DatasetRequired()
        serializer.save(dataset=dataset)
