from django.urls import path

from musicbox_core.apps.interactions.api.views import (
    CommentLikeView,
    CommentView,
    PostCommentsView,
    PostLikeView,
    ReviewCommentsView,
    ReviewLikeView,
)

urlpatterns = [
    path("posts/<str:target_id>/comments", PostCommentsView.as_view(), name="post-comments"),
    path("reviews/<str:target_id>/comments", ReviewCommentsView.as_view(), name="review-comments"),
    path("comments/<str:comment_id>", CommentView.as_view(), name="comment-detail"),
    path("posts/<str:target_id>/like", PostLikeView.as_view(), name="post-like"),
    path("reviews/<str:target_id>/like", ReviewLikeView.as_view(), name="review-like"),
    path("comments/<str:target_id>/like", CommentLikeView.as_view(), name="comment-like"),
]
