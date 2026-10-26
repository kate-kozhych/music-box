from django.urls import path

from innotter_core.apps.profiles.api.views import PublicProfileView

urlpatterns = [
    path("users/<str:username>", PublicProfileView.as_view(), name="public-profile"),
]
