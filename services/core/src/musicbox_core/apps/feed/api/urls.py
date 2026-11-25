from django.urls import path

from musicbox_core.apps.feed.api.views import ExploreFeedView, HomeFeedView

urlpatterns = [
    path("feed", HomeFeedView.as_view(), name="feed"),
    path("feed/explore", ExploreFeedView.as_view(), name="feed-explore"),
]
