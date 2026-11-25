from django.urls import path

from musicbox_core.apps.posts.api.views import PostsView, PostView, UserPostsView

urlpatterns = [
    path("posts", PostsView.as_view(), name="posts"),
    path("posts/<str:post_id>", PostView.as_view(), name="post-detail"),
    path("users/<str:username>/posts", UserPostsView.as_view(), name="user-posts"),
]
