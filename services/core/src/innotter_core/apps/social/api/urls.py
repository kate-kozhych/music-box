from django.urls import path

from innotter_core.apps.social.api.views import FollowersView, FollowingView, FollowView

urlpatterns = [
    path("users/<str:username>/follow", FollowView.as_view(), name="follow"),
    path("users/<str:username>/followers", FollowersView.as_view(), name="followers"),
    path("users/<str:username>/following", FollowingView.as_view(), name="following"),
]
