from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from musicbox_common.adapters.django import problem_view
from musicbox_common.errors import ErrorCode
from musicbox_core.apps.platform.views import live, ready

urlpatterns = [
    path("health/live", live),
    path("health/ready", ready),
    path("api/v1/schema", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/v1/docs",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/v1/", include("musicbox_core.apps.catalog.api.urls")),
    path("api/v1/", include("musicbox_core.apps.ratings.api.urls")),
    path("api/v1/", include("musicbox_core.apps.reviews.api.urls")),
    path("api/v1/", include("musicbox_core.apps.diary.api.urls")),
    path("api/v1/", include("musicbox_core.apps.posts.api.urls")),
    path("api/v1/", include("musicbox_core.apps.interactions.api.urls")),
    path("api/v1/", include("musicbox_core.apps.feed.api.urls")),
    path("api/v1/", include("musicbox_core.apps.moderation.api.urls")),
    path("api/v1/", include("musicbox_core.apps.profiles.api.urls")),
    path("api/v1/", include("musicbox_core.apps.social.api.urls")),
]

handler404 = problem_view(404, ErrorCode.NOT_FOUND, "The requested resource was not found.")
handler500 = problem_view(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")
