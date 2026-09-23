from django.db import connection
from drf_spectacular.utils import extend_schema
from redis import Redis
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class ReadinessSerializer(serializers.Serializer):
    status = serializers.CharField()
    database = serializers.BooleanField()
    broker = serializers.BooleanField()


class ReadinessView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(responses={200: ReadinessSerializer, 503: ReadinessSerializer}, tags=["health"])
    def get(self, request):
        from django.conf import settings

        database = broker = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                database = cursor.fetchone()[0] == 1
        except Exception:
            pass
        try:
            with Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=1,
                                socket_timeout=1) as redis:
                broker = bool(redis.ping())
        except Exception:
            pass
        ready = database and broker
        return Response({"status": "ok" if ready else "unavailable",
                         "database": database, "broker": broker}, status=200 if ready else 503)
