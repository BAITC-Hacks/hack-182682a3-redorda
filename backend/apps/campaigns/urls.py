from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .dataset_views import DatasetDemoImportView, DatasetUploadView
from .views import CurrentDatasetView, HealthView, MetaView, RunViewSet

router = DefaultRouter()
router.register("runs", RunViewSet, basename="run")
urlpatterns = [
    path("health/", HealthView.as_view()),
    path("meta/", MetaView.as_view()),
    path("datasets/current/", CurrentDatasetView.as_view()),
    path("datasets/import/", DatasetUploadView.as_view()),
    path("datasets/import-demo/", DatasetDemoImportView.as_view()),
    path("", include(router.urls)),
]
