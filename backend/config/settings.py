import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
BASE_DIR = ROOT_DIR / "backend"
load_dotenv(ROOT_DIR / ".env")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
REDORDA_REQUIRE_AUTH = not DEBUG or os.getenv("REDORDA_REQUIRE_AUTH", "0") == "1"
REDORDA_RUN_EXECUTION_ENABLED = os.getenv("REDORDA_RUN_EXECUTION_ENABLED", "0") == "1"
REDORDA_ENVIRONMENT_FACTORY = os.getenv("REDORDA_ENVIRONMENT_FACTORY", "")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-development-only-change-before-deployment")
if not DEBUG and SECRET_KEY == "local-development-only-change-before-deployment":
    raise RuntimeError("Set DJANGO_SECRET_KEY before running with DJANGO_DEBUG=0")
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend").split(",")
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
CSRF_FAILURE_VIEW = "apps.campaigns.auth_views.csrf_failure"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 12 * 60 * 60
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
if not DEBUG:
    # Production listeners are loopback-only, behind our trusted HTTPS proxy.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "drf_spectacular", "apps.campaigns",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
# Resolve relative SQLite paths against the repo, independent of the current working directory.
database_url = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
DATABASES = {"default": dj_database_url.parse(database_url, conn_max_age=60)}
if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    database_name = Path(DATABASES["default"]["NAME"])
    if not database_name.is_absolute():
        DATABASES["default"]["NAME"] = ROOT_DIR / database_name
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Asia/Almaty"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "apps.campaigns.errors.api_exception_handler",
    "DEFAULT_PERMISSION_CLASSES": ["config.permissions.WorkspaceAccess"],
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_THROTTLE_RATES": {"login": "10/min"},
}
SPECTACULAR_SETTINGS = {
    "TITLE": "RedOrda Campaign API", "VERSION": "1.0.0",
    "SERVE_PERMISSIONS": ["config.permissions.WorkspaceAccess"],
    "SERVE_AUTHENTICATION": ["rest_framework.authentication.SessionAuthentication"],
}
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_SOFT_TIME_LIMIT = 570
CELERY_TASK_TRACK_STARTED = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_DEFAULT_QUEUE = os.getenv("REDORDA_QUEUE", "redorda")
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_TIMEOUT = 3
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "socket_connect_timeout": 3, "socket_timeout": 3,
    "global_keyprefix": os.getenv("REDORDA_REDIS_PREFIX", "redorda:"),
}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {
    "global_keyprefix": os.getenv("REDORDA_REDIS_PREFIX", "redorda:"),
}
CELERY_RESULT_EXPIRES = 3600
if os.getenv("CACHE_URL"):
    CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ["CACHE_URL"],
        "KEY_PREFIX": "redorda-cache",
    }}
