from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from .serializers import ErrorSerializer

CSRF_HEADER = OpenApiParameter("X-CSRFToken", str, OpenApiParameter.HEADER, required=True,
                               description="Токен из auth/csrf/; после login использовать новый.")


class UserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    is_staff = serializers.BooleanField()


class SessionSerializer(serializers.Serializer):
    authenticated = serializers.BooleanField()
    user = UserSerializer(allow_null=True)
    csrf_token = serializers.CharField(required=False)


class CsrfSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=512, trim_whitespace=False, write_only=True)


class InvalidCredentials(APIException):
    status_code = 401
    default_code = "invalid_credentials"
    default_detail = "Неверное имя пользователя или пароль."


class LoginThrottle(SimpleRateThrottle):
    scope = "login"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


def session_payload(request, *, include_csrf=False):
    authenticated = request.user.is_authenticated
    payload = {"authenticated": authenticated,
               "user": UserSerializer(request.user).data if authenticated else None}
    if include_csrf:
        payload["csrf_token"] = get_token(request)
    return payload


def csrf_failure(request, reason=""):
    return JsonResponse({"error": {"code": "csrf_failed",
                                   "message": "Обновите CSRF-токен и повторите запрос.",
                                   "fields": {}}}, status=403)


@method_decorator(never_cache, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=CsrfSerializer, tags=["auth"])
    def get(self, request):
        return Response({"csrf_token": get_token(request)})


@method_decorator(never_cache, name="dispatch")
class MeView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=SessionSerializer, tags=["auth"])
    def get(self, request):
        return Response(session_payload(request))


@method_decorator([csrf_protect, never_cache], name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [LoginThrottle]

    @extend_schema(request=LoginSerializer, parameters=[CSRF_HEADER], tags=["auth"], responses={
        200: SessionSerializer, 400: ErrorSerializer, 401: ErrorSerializer,
        403: ErrorSerializer, 429: ErrorSerializer,
    })
    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate(request, **data.validated_data)
        if user is None:
            raise InvalidCredentials()
        login(request, user)
        return Response(session_payload(request, include_csrf=True))


@method_decorator([csrf_protect, never_cache], name="dispatch")
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, parameters=[CSRF_HEADER], tags=["auth"],
                   responses={200: SessionSerializer, 403: ErrorSerializer})
    def post(self, request):
        logout(request)
        return Response(session_payload(request, include_csrf=True))
