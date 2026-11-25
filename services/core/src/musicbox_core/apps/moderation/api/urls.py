from django.urls import path

from musicbox_core.apps.moderation.api.views import HideView, ModerationLogView, RestoreView

urlpatterns = [
    path("admin/moderation/log", ModerationLogView.as_view(), name="moderation-log"),
    path(
        "admin/moderation/<str:content_type>/<str:content_id>/hide",
        HideView.as_view(),
        name="moderation-hide",
    ),
    path(
        "admin/moderation/<str:content_type>/<str:content_id>/restore",
        RestoreView.as_view(),
        name="moderation-restore",
    ),
]
