from apps.campaigns.auth_views import CsrfView, LoginView, LogoutView, MeView
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from config.health import ReadinessView

urlpatterns = [
    path("api/v1/ready/", ReadinessView.as_view()),
    path("api/v1/auth/csrf/", CsrfView.as_view()),
    path("api/v1/auth/login/", LoginView.as_view()),
    path("api/v1/auth/logout/", LogoutView.as_view()),
    path("api/v1/auth/me/", MeView.as_view()),
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.campaigns.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
