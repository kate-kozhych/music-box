from django.urls import path

from musicbox_core.apps.ratings.api.views import AlbumRatingView, UserRatingsView

urlpatterns = [
    path("albums/<str:album_id>/rating", AlbumRatingView.as_view(), name="album-rating"),
    path("users/<str:username>/ratings", UserRatingsView.as_view(), name="user-ratings"),
]
