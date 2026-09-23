from django.conf import settings
from rest_framework.permissions import BasePermission


class WorkspaceAccess(BasePermission):
    """Public deployments require a session; local development can stay anonymous."""

    def has_permission(self, request, view):
        return not settings.REDORDA_REQUIRE_AUTH or bool(
            request.user and request.user.is_authenticated and request.user.is_active
        )
