from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .team_views import CommandDetailView, CommandListView, TeamView
from .views import CurrentDatasetView, HealthView, MetaView, RunViewSet

router = DefaultRouter()
router.register("runs", RunViewSet, basename="run")
urlpatterns = [
    path("health/", HealthView.as_view()),
    path("meta/", MetaView.as_view()),
    path("datasets/current/", CurrentDatasetView.as_view()),
    path("runs/<uuid:id>/team/", TeamView.as_view()),
    path("runs/<uuid:id>/commands/", CommandListView.as_view()),
    path("runs/<uuid:id>/commands/<str:command_id>/", CommandDetailView.as_view()),
    path("", include(router.urls)),
]
