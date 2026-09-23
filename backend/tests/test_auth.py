import secrets
from unittest.mock import patch

import pytest
from apps.campaigns.models import CampaignRun, Dataset
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def public_mode(settings):
    settings.REDORDA_REQUIRE_AUTH = True
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()


@pytest.fixture
def credentials():
    password = secrets.token_urlsafe(24)
    user = get_user_model().objects.create_user(username="operator", password=password)
    return user, password


def csrf(client):
    response = client.get("/api/v1/auth/csrf/")
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert "csrftoken" in client.cookies
    return response.data["csrf_token"]


def sign_in(client, credentials):
    user, password = credentials
    response = client.post("/api/v1/auth/login/", {"username": user.username, "password": password},
                           format="json", HTTP_X_CSRFTOKEN=csrf(client))
    assert response.status_code == 200, response.data
    return response


@pytest.mark.parametrize("url", ["/api/v1/meta/", "/api/v1/datasets/current/",
                                  "/api/v1/runs/", "/api/schema/", "/api/docs/"])
def test_anonymous_cannot_read_workspace(url):
    response = APIClient().get(url)
    assert response.status_code == 403
    assert response.data["error"]["code"] == "not_authenticated"


def test_health_and_session_discovery_are_public():
    client = APIClient()
    assert client.get("/api/v1/health/").status_code == 200
    assert client.get("/api/v1/auth/me/").data == {"authenticated": False, "user": None}


def test_login_requires_csrf_even_for_anonymous(credentials):
    user, password = credentials
    response = APIClient(enforce_csrf_checks=True).post(
        "/api/v1/auth/login/", {"username": user.username, "password": password}, format="json")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_session_csrf_rotation_write_and_logout(credentials):
    client = APIClient(enforce_csrf_checks=True)
    old_token = csrf(client)
    old_cookie = client.cookies["csrftoken"].value
    response = sign_in(client, credentials)
    new_token = response.data["csrf_token"]
    assert client.cookies["csrftoken"].value != old_cookie
    assert client.cookies["sessionid"]["httponly"]
    assert response.data["authenticated"] is True
    assert "password" not in response.data["user"]
    assert client.get("/api/v1/auth/me/").data["user"]["username"] == "operator"
    assert client.get("/api/v1/meta/").status_code == 200
    Dataset.objects.create(name="test", checksum="a" * 64, source_dir="/unused", customer_count=10)
    for token in (None, old_token):
        kwargs = {"HTTP_X_CSRFTOKEN": token} if token else {}
        assert client.post("/api/v1/runs/", {"name": "Denied"}, **kwargs).status_code == 403
    created = client.post("/api/v1/runs/", {"name": "Allowed"}, HTTP_X_CSRFTOKEN=new_token)
    assert created.status_code == 201, created.data
    assert client.post("/api/v1/auth/logout/").status_code == 403
    response = client.post("/api/v1/auth/logout/", HTTP_X_CSRFTOKEN=new_token)
    assert response.status_code == 200
    assert response.data["authenticated"] is False
    assert client.get("/api/v1/runs/").status_code == 403


@pytest.mark.parametrize("inactive", [False, True])
def test_invalid_credentials_do_not_create_a_session(credentials, inactive):
    user, password = credentials
    if inactive:
        user.is_active = False
        user.save()
    client = APIClient(enforce_csrf_checks=True)
    response = client.post("/api/v1/auth/login/", {
        "username": user.username, "password": password if inactive else secrets.token_urlsafe(24),
    }, HTTP_X_CSRFTOKEN=csrf(client))
    assert response.status_code == 401
    assert response.data["error"]["code"] == "invalid_credentials"
    assert "sessionid" not in client.cookies


def test_login_rate_limit():
    client = APIClient(enforce_csrf_checks=True)
    token = csrf(client)
    with patch("apps.campaigns.auth_views.authenticate", return_value=None):
        for _ in range(10):
            assert client.post("/api/v1/auth/login/", {"username": "absent", "password": "invalid"},
                               HTTP_X_CSRFTOKEN=token).status_code == 401
        response = client.post("/api/v1/auth/login/", {"username": "absent", "password": "invalid"},
                               HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 429


def test_unavailable_engine_does_not_queue(settings, credentials):
    settings.REDORDA_RUN_EXECUTION_ENABLED = False
    client = APIClient()
    client.force_authenticate(user=credentials[0])
    dataset = Dataset.objects.create(name="test", checksum="b" * 64,
                                     source_dir="/unused", customer_count=10)
    run = CampaignRun.objects.create(name="Plan", dataset=dataset)
    with patch("apps.campaigns.services.execution._publish") as publish:
        response = client.post(f"/api/v1/runs/{run.pk}/start/", HTTP_IDEMPOTENCY_KEY="test")
    assert response.status_code == 503
    assert response.data["error"]["code"] == "engine_unavailable"
    assert not publish.called
    run.refresh_from_db()
    assert run.status == "draft"
    assert client.get("/api/v1/meta/").data["features"]["run_execution"] is False


def test_readiness_does_not_leak_broker_errors():
    with patch("config.health.Redis.from_url", side_effect=OSError("private broker address")):
        response = APIClient().get("/api/v1/ready/")
    assert response.status_code == 503
    assert response.data == {"status": "unavailable", "database": True, "broker": False}
