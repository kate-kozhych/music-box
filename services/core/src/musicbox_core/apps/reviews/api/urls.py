from django.urls import path

from musicbox_core.apps.reviews.api.views import AlbumReviewsView, ReviewView, UserReviewsView

urlpatterns = [
    path("albums/<str:album_id>/reviews", AlbumReviewsView.as_view(), name="album-reviews"),
    path("reviews/<str:review_id>", ReviewView.as_view(), name="review-detail"),
    path("users/<str:username>/reviews", UserReviewsView.as_view(), name="user-reviews"),
]
